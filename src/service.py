"""Une todo el flujo de una consulta:

consulta -> prompt -> proveedor LLM -> parseo + validación Pydantic (con reintentos) -> métricas
"""
import json

from pydantic import ValidationError

from src.metrics import log_request
from src.prompts import load_prompt
from src.providers import API_ERRORS, get_provider
from src.schemas import TriageResponse

# Reintentos ante una respuesta que no respeta el contrato JSON.
# (Los errores de red y rate limits los reintenta el SDK; ver providers.py.)
MAX_ATTEMPTS = 3


class TriageError(Exception):
    """Error esperado y explicable: la CLI lo muestra como JSON de error, sin traceback."""


def parse_response(text: str) -> TriageResponse:
    """Extrae el objeto JSON del texto del modelo y lo valida contra el contrato.

    Algunos modelos envuelven el JSON en ```json ... ``` o agregan texto: tomamos
    desde la primera '{' hasta la última '}'.
    """
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        raise ValueError("la respuesta no contiene un objeto JSON")
    data = json.loads(text[start : end + 1])
    return TriageResponse.model_validate(data)


def describe_error(error: Exception) -> str:
    """Mensaje corto para el modelo: qué campo falló y por qué."""
    if isinstance(error, ValidationError):
        return "; ".join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in error.errors())
    return str(error)


def triage(query: str, provider_name: str, prompt_version: str = "v1") -> TriageResponse:
    if not query.strip():
        raise TriageError("La consulta está vacía.")

    try:
        provider = get_provider(provider_name)
        prompt = load_prompt(prompt_version)
    except ValueError as e:  # proveedor o prompt inexistente, o falta una clave
        raise TriageError(str(e)) from e

    system = prompt.system
    user = prompt.render_user(query)
    last_error = None

    for attempt in range(1, MAX_ATTEMPTS + 1):
        if last_error:
            # La corrección va en el system prompt: es una instrucción nuestra, no un dato del cliente.
            system = prompt.system + (
                f"\n\nTu respuesta anterior no respetó el contrato ({last_error}). "
                "Respondé solo con el objeto JSON corregido."
            )
        try:
            result = provider.complete(system, user)
        except API_ERRORS as e:
            raise TriageError(f"Error del proveedor {provider_name}: {type(e).__name__}: {e}") from e

        try:
            response = parse_response(result.text)
        except (ValueError, ValidationError) as e:  # json.JSONDecodeError es un ValueError
            last_error = describe_error(e)
            log_request(result, prompt.version, attempt, valid=False, category=None)
            continue

        log_request(result, prompt.version, attempt, valid=True, category=response.category)
        return response

    raise TriageError(f"Se agotaron los {MAX_ATTEMPTS} intentos sin una respuesta válida. Último error: {last_error}")
