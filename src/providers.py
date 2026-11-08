"""Clientes LLM: una interfaz común y tres proveedores (Anthropic, Groq y NVIDIA).

Groq y NVIDIA comparten una sola clase (OpenAICompatibleProvider): solo cambian base_url, clave y modelo.

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


@dataclass(frozen=True)
class OpenAICompatibleSpec:
    """Lo que cambia entre proveedores con API compatible con OpenAI. Todo se lee del entorno."""
    name: str
    base_url: str
    key_env: str
    model_env: str
    default_model: str
    reasoning_env: str
    default_reasoning: str
    structured_env: str
    default_structured: str


# Groq: valores verificados en el proyecto base (07/11/2026), 418 llamadas reales.
# NVIDIA (build.nvidia.com): el endpoint https://integrate.api.nvidia.com/v1/chat/completions, el header
#   "Authorization: Bearer $NVIDIA_API_KEY" y el ID mistralai/mistral-large-4-1t-a52b figuran en la página
#   oficial de Mistral Large 4 (vista el 07/11/2026). Todavía SIN probar con una llamada real: fase 1.
#   reasoning_effort vacío = no se manda (no todos los modelos de NVIDIA lo aceptan; sin verificar).
SPECS = {
    "groq": OpenAICompatibleSpec(
        name="groq", base_url="https://api.groq.com/openai/v1", key_env="GROQ_API_KEY",
        model_env="GROQ_MODEL", default_model="openai/gpt-oss-120b",
        reasoning_env="GROQ_REASONING_EFFORT", default_reasoning="low",
        structured_env="GROQ_STRUCTURED", default_structured="json_schema",
    ),
    "nvidia": OpenAICompatibleSpec(
        name="nvidia", base_url="https://integrate.api.nvidia.com/v1", key_env="NVIDIA_API_KEY",
        model_env="NVIDIA_MODEL", default_model="openai/gpt-oss-20b",
        reasoning_env="NVIDIA_REASONING_EFFORT", default_reasoning="",
        structured_env="NVIDIA_STRUCTURED", default_structured="json_schema",
    ),
}

# Cómo se pide la salida estructurada:
#   json_schema = el proveedor fuerza el schema del contrato (lo que usa Groq);
#   json_object = solo fuerza "un JSON" (el schema va en el prompt y lo valida Pydantic);
#   off         = no se pide nada (Pydantic y los reintentos del servicio hacen todo el trabajo).
STRUCTURED_MODES = ("json_schema", "json_object", "off")


class OpenAICompatibleProvider(LLMProvider):
    """Cualquier API compatible con OpenAI (Groq, NVIDIA): mismo SDK, cambia base_url y la clave."""

    def __init__(self, spec: OpenAICompatibleSpec, model: str, max_tokens: int, temperature: float,
                 reasoning_effort: str = "", structured: str = "json_schema", base_url: str | None = None):
        super().__init__(model, max_tokens)
        if structured not in STRUCTURED_MODES:
            raise ValueError(f"{spec.structured_env} inválido: {structured!r}. Opciones: {', '.join(STRUCTURED_MODES)}")
        self.name = spec.name
        self.temperature = temperature
        self.reasoning_effort = reasoning_effort
        self.structured = structured
        self.base_url = base_url or spec.base_url
        self.client = openai.OpenAI(
            api_key=_require_env(spec.key_env),
            base_url=self.base_url,
            max_retries=SDK_MAX_RETRIES,
            timeout=TIMEOUT_S,
        )

    def build_request(self, system: str, user: str, schema: dict) -> dict:
        """Arma los parámetros de la llamada. Separado de complete() para poder testearlo sin red."""
        request = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "temperature": self.temperature,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        if self.reasoning_effort:
            request["reasoning_effort"] = self.reasoning_effort
        if self.structured == "json_schema":
            request["response_format"] = {"type": "json_schema", "json_schema": {
                "name": "respuesta", "schema": schema, "strict": True}}
        elif self.structured == "json_object":
            request["response_format"] = {"type": "json_object"}
        return request

    def complete(self, system: str, user: str, schema: dict | None = None) -> LLMResult:
        schema = schema or output_json_schema()
        start = time.perf_counter()
        response = self.client.chat.completions.create(**self.build_request(system, user, schema))
        latency = time.perf_counter() - start
        usage = response.usage
        return LLMResult(
            text=response.choices[0].message.content or "",
            provider=self.name,
            model=self.model,
            prompt_tokens=usage.prompt_tokens if usage else 0,
            completion_tokens=usage.completion_tokens if usage else 0,
            latency_s=latency,
            stop_reason=str(response.choices[0].finish_reason),
        )


PROVIDERS = ("anthropic", *SPECS)
# Proveedores que cobran por token. Los runners avisan antes de llamarlos y no los usan por defecto.
PAID_PROVIDERS = {"anthropic"}
# Versión gratuita: Anthropic sigue en el código (lo usó el proyecto base), pero no se llama salvo que
# se pida explícitamente con --provider anthropic o MODERATOR_PROVIDER=anthropic.
FREE_PROVIDERS = tuple(p for p in PROVIDERS if p not in PAID_PROVIDERS)


def get_provider(name: str, model: str | None = None, max_tokens: int | None = None) -> LLMProvider:
    """Crea el proveedor pedido leyendo modelo y parámetros del entorno (.env).

    model: pisa el modelo del .env (lo usa la prueba de humo para recorrer varios modelos).
    max_tokens: pisa MAX_TOKENS (lo usa el moderador, con MODERATION_MAX_TOKENS).
    """
    max_tokens = max_tokens or int(os.getenv("MAX_TOKENS", "500"))
    if name == "anthropic":
        return AnthropicProvider(model or os.getenv("ANTHROPIC_MODEL", "claude-haiku-5-5"), max_tokens,
                                 os.getenv("ANTHROPIC_THINKING", "disabled"))
    if name in SPECS:
        spec = SPECS[name]
        return OpenAICompatibleProvider(
            spec,
            model=model or os.getenv(spec.model_env, spec.default_model),
            max_tokens=max_tokens,
            temperature=float(os.getenv("TEMPERATURE", "0")),
            reasoning_effort=os.getenv(spec.reasoning_env, spec.default_reasoning).strip(),
            structured=os.getenv(spec.structured_env, spec.default_structured).strip(),
        )
    raise ValueError(f"Proveedor desconocido: {name!r}. Opciones: {', '.join(PROVIDERS)}")


def _require_env(var: str) -> str:
    value = os.getenv(var, "").strip()
    if not value or value.startswith("tu_clave"):
        raise ValueError(f"Falta configurar {var} en el archivo .env (ver .env.example).")
    return value
