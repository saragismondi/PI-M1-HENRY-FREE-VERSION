"""Contrato de salida del servicio de triage.

Toda respuesta del modelo se valida contra TriageResponse antes de devolverla.
Si no cumple, el servicio reintenta (ver service.py).
"""
import json
from typing import Literal

from pydantic import BaseModel, Field

# Única fuente de verdad de las categorías: la usan el contrato, los prompts y los evals.
CATEGORIES = ("billing", "technical", "account", "other")
Category = Literal["billing", "technical", "account", "other"]


class TriageResponse(BaseModel):
    answer: str = Field(min_length=1, description="Respuesta breve propuesta para el cliente.")
    confidence: float = Field(ge=0.0, le=1.0, description="Confianza en la categoría, de 0 a 1.")
    category: Category
    actions: list[str] = Field(
        min_length=1,
        description="Acciones sugeridas para el agente humano. El servicio no las ejecuta.",
    )


# Restricciones que la API de Anthropic rechaza en salidas estructuradas (verificado: devuelve 400
# "For 'number' type, properties maximum, minimum are not supported"). Se quitan del schema que
# se envía al proveedor; Pydantic las sigue validando igual cuando llega la respuesta.
_UNSUPPORTED_KEYS = ("minimum", "maximum", "minLength", "minItems", "title")


def _strip(node):
    if isinstance(node, dict):
        return {k: _strip(v) for k, v in node.items() if k not in _UNSUPPORTED_KEYS}
    if isinstance(node, list):
        return [_strip(x) for x in node]
    return node


def json_schema_for(model: type[BaseModel]) -> dict:
    """JSON schema de un modelo Pydantic, en la forma que aceptan las salidas estructuradas."""
    schema = _strip(model.model_json_schema())
    schema["additionalProperties"] = False
    return schema


def output_json_schema() -> dict:
    """JSON schema del contrato del triage."""
    return json_schema_for(TriageResponse)


def extract_json(text: str) -> dict:
    """Toma desde la primera '{' hasta la última '}' (por si el modelo agrega texto o ```json)."""
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        raise ValueError("la respuesta no contiene un objeto JSON")
    return json.loads(text[start : end + 1])
