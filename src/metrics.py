"""Registro de métricas: una línea JSON por request al LLM, append-only, en data/metrics.jsonl.

Costo estimado (USD) = prompt_tokens / 1_000_000 * precio_entrada
                     + completion_tokens / 1_000_000 * precio_salida
Cualquiera puede rehacer la cuenta con los tokens de cada línea y la tabla PRICES_PER_MTOK.
"""
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from src.providers import LLMResult

DEFAULT_METRICS_PATH = Path(__file__).resolve().parent.parent / "data" / "metrics.jsonl"

# USD por 1 millón de tokens: (entrada, salida).
# - Anthropic: platform.claude.com/docs/en/about-claude/pricing, consultado el 07/11/2026.
#   Fila "Claude Haiku 5.5 (for prompts up to 100,000 tokens)": $0.10 entrada, $0.50 salida.
# - Groq: usamos el plan gratuito, así que el costo real es 0. (El 07/11/2026 la API de Groq
#   no habilitó llama-3.3-70b-versatile para esta cuenta: se lista con client.models.list()).
PRICES_PER_MTOK = {
    "claude-haiku-5-5": (0.10, 0.50),
    "openai/gpt-oss-20b": (0.0, 0.0),
}


def estimate_cost_usd(model: str, prompt_tokens: int, completion_tokens: int) -> float | None:
    """Devuelve None si el modelo no está en la tabla: preferimos 'sin dato' a inventar un número."""
    if model not in PRICES_PER_MTOK:
        return None
    price_in, price_out = PRICES_PER_MTOK[model]
    return round(prompt_tokens / 1_000_000 * price_in + completion_tokens / 1_000_000 * price_out, 8)


def log_request(result: LLMResult, prompt_version: str, attempt: int, valid: bool, category: str | None) -> dict:
    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "provider": result.provider,
        "model": result.model,
        "prompt_version": prompt_version,
        "attempt": attempt,
        "valid_contract": valid,
        "category": category,
        "prompt_tokens": result.prompt_tokens,
        "completion_tokens": result.completion_tokens,
        "total_tokens": result.prompt_tokens + result.completion_tokens,
        "latency_s": round(result.latency_s, 3),
        "cost_usd": estimate_cost_usd(result.model, result.prompt_tokens, result.completion_tokens),
    }
    path = Path(os.getenv("METRICS_PATH", DEFAULT_METRICS_PATH))
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:  # "a" = append: nunca se borra lo anterior
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
    return record
