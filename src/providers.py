"""Clientes LLM: una interfaz común y dos proveedores (Anthropic y Groq).

El resto del proyecto solo llama a `provider.complete(system, user)`.
Cambiar de proveedor no toca la lógica de negocio: solo cambia el nombre que se pasa a get_provider().
"""
import os
import time
from dataclasses import dataclass

import anthropic
import openai

from src.schemas import output_json_schema

# Errores de API por proveedor. service.py los atrapa para que la CLI no se caiga.
API_ERRORS = (anthropic.APIError, openai.APIError)

# Los dos SDK reintentan solos ante 408, 409, 429 (rate limit) y 5xx, con espera exponencial.
# Verificado en el código instalado (_base_client.py, método _should_retry). El valor por defecto es 2;
# lo subimos a 3 para tolerar mejor los límites del plan gratis de Groq.
SDK_MAX_RETRIES = 3
TIMEOUT_S = 30

# Por qué salida estructurada y razonamiento acotado (diagnóstico del 07/11/2026):
# claude-haiku-5-5 y openai/gpt-oss-20b razonan antes de responder, y esos tokens cuentan dentro
# de MAX_TOKENS. En los evals, las respuestas inválidas usaron exactamente 500 tokens: el razonamiento
# se comía el tope y el JSON quedaba cortado. En vez de subir el tope:
#   - pedimos salida estructurada con el schema del contrato (el modelo no puede escribir texto libre);
#   - Anthropic: thinking desactivado por defecto (ANTHROPIC_THINKING=adaptive lo vuelve a prender);
#   - Groq: reasoning_effort bajo por defecto (GROQ_REASONING_EFFORT=low|medium|high).


@dataclass
class LLMResult:
    """Lo que devuelve cualquier proveedor, siempre con la misma forma."""
    text: str
    provider: str
    model: str
    prompt_tokens: int
    completion_tokens: int
    latency_s: float
    stop_reason: str = "?"  # por qué terminó: "end_turn"/"stop" = solo; "max_tokens"/"length" = lo cortó el tope


class LLMProvider:
    """Interfaz común. Cada proveedor implementa complete()."""
    name = "base"

    def __init__(self, model: str, max_tokens: int):
        self.model = model
        self.max_tokens = max_tokens

    def complete(self, system: str, user: str, schema: dict | None = None) -> LLMResult:
        """schema: JSON schema de la salida estructurada. Por defecto, el contrato del triage."""
        raise NotImplementedError


class AnthropicProvider(LLMProvider):
    name = "anthropic"

    def __init__(self, model: str, max_tokens: int, thinking: str = "disabled"):
        super().__init__(model, max_tokens)
        self.thinking = thinking
        self.client = anthropic.Anthropic(
            api_key=_require_env("ANTHROPIC_API_KEY"), max_retries=SDK_MAX_RETRIES, timeout=TIMEOUT_S
        )

    def complete(self, system: str, user: str, schema: dict | None = None) -> LLMResult:
        schema = schema or output_json_schema()
        start = time.perf_counter()
        # Nota: en anthropic 2.6.0, messages.create() no acepta `temperature` (verificado en la firma del SDK).
        response = self.client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            system=system,
            messages=[{"role": "user", "content": user}],
            thinking={"type": self.thinking},
            output_config={"format": {"type": "json_schema", "schema": schema}},
        )
        latency = time.perf_counter() - start
        text = "".join(block.text for block in response.content if block.type == "text")
        return LLMResult(
            text=text,
            provider=self.name,
            model=self.model,
            prompt_tokens=response.usage.input_tokens,
            completion_tokens=response.usage.output_tokens,
            latency_s=latency,
            stop_reason=str(response.stop_reason),
        )


class GroqProvider(LLMProvider):
    """Groq expone una API compatible con OpenAI: usamos el SDK de openai cambiando base_url."""
    name = "groq"
    BASE_URL = "https://api.groq.com/openai/v1"

    def __init__(self, model: str, max_tokens: int, temperature: float, reasoning_effort: str = "low"):
        super().__init__(model, max_tokens)
        self.temperature = temperature
        self.reasoning_effort = reasoning_effort
        self.client = openai.OpenAI(
            api_key=_require_env("GROQ_API_KEY"),
            base_url=self.BASE_URL,
            max_retries=SDK_MAX_RETRIES,
            timeout=TIMEOUT_S,
        )

    def complete(self, system: str, user: str, schema: dict | None = None) -> LLMResult:
        schema = schema or output_json_schema()
        start = time.perf_counter()
        response = self.client.chat.completions.create(
            model=self.model,
            max_tokens=self.max_tokens,
            temperature=self.temperature,
            reasoning_effort=self.reasoning_effort,
            response_format={"type": "json_schema", "json_schema": {
                "name": "respuesta", "schema": schema, "strict": True}},
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )
        latency = time.perf_counter() - start
        return LLMResult(
            text=response.choices[0].message.content or "",
            provider=self.name,
            model=self.model,
            prompt_tokens=response.usage.prompt_tokens,
            completion_tokens=response.usage.completion_tokens,
            latency_s=latency,
            stop_reason=str(response.choices[0].finish_reason),
        )


PROVIDERS = ("anthropic", "groq")


def get_provider(name: str) -> LLMProvider:
    """Crea el proveedor pedido leyendo modelo y parámetros del entorno (.env)."""
    max_tokens = int(os.getenv("MAX_TOKENS", "500"))
    if name == "anthropic":
        return AnthropicProvider(os.getenv("ANTHROPIC_MODEL", "claude-haiku-5-5"), max_tokens,
                                 os.getenv("ANTHROPIC_THINKING", "disabled"))
    if name == "groq":
        temperature = float(os.getenv("TEMPERATURE", "0"))
        return GroqProvider(os.getenv("GROQ_MODEL", "openai/gpt-oss-120b"), max_tokens, temperature,
                            os.getenv("GROQ_REASONING_EFFORT", "low"))
    raise ValueError(f"Proveedor desconocido: {name!r}. Opciones: {', '.join(PROVIDERS)}")


def _require_env(var: str) -> str:
    value = os.getenv(var, "").strip()
    if not value or value.startswith("tu_clave"):
        raise ValueError(f"Falta configurar {var} en el archivo .env (ver .env.example).")
    return value
