"""Contrato de salida del servicio de triage.

Toda respuesta del modelo se valida contra TriageResponse antes de devolverla.
Si no cumple, el servicio reintenta (ver service.py).
"""
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
