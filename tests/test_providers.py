"""Tests del proveedor genérico OpenAI-compatible (Groq, NVIDIA), del registro de errores de API y
del aviso de proveedores pagos. No llaman a ninguna API: usan un cliente falso. Costo 0.

Uso (sin dependencias extra):
    python tests/test_providers.py
"""
import json
import os
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))  # permite correrlo como `python tests/test_providers.py`

import httpx2  # noqa: E402  (el cliente HTTP que usa openai 3.46.0)
import openai  # noqa: E402

from src import service  # noqa: E402
from src.metrics import estimate_cost_usd  # noqa: E402
from src.providers import (FREE_PROVIDERS, PAID_PROVIDERS, PROVIDERS, SPECS,  # noqa: E402
                           OpenAICompatibleProvider, get_provider)
from src.schemas import output_json_schema  # noqa: E402

VALID = json.dumps({"answer": "Vamos a revisar el cobro.", "confidence": 0.9, "category": "billing",
                    "actions": ["Revisar el historial de pagos."]})


@contextmanager
def env(**values):
    """Pone variables de entorno durante el test y deja las originales al terminar."""
    old = {k: os.environ.get(k) for k in values}
    os.environ.update({k: v for k, v in values.items() if v is not None})
    for k, v in values.items():
        if v is None:
            os.environ.pop(k, None)
    try:
        yield
    finally:
        for k, v in old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


@contextmanager
def temp_metrics():
    """Métricas en un archivo temporal: los tests no ensucian data/metrics.jsonl."""
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "metrics.jsonl"
        with env(METRICS_PATH=str(path)):
            yield path


def read_lines(path: Path) -> list[dict]:
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []


def fake_completion(text: str):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=text), finish_reason="stop")],
        usage=SimpleNamespace(prompt_tokens=100, completion_tokens=50),
    )


def rate_limit_error() -> openai.RateLimitError:
    request = httpx2.Request("POST", "https://integrate.api.nvidia.com/v1/chat/completions")
    return openai.RateLimitError("Too Many Requests", response=httpx2.Response(429, request=request), body=None)


# --- Construcción del proveedor ----------------------------------------------

def test_nvidia_esta_registrado_y_es_gratis():
    assert "nvidia" in PROVIDERS and "groq" in PROVIDERS
    assert "nvidia" not in PAID_PROVIDERS and "anthropic" in PAID_PROVIDERS


def test_nvidia_sin_clave_da_error_claro():
    with env(NVIDIA_API_KEY=None):
        try:
            get_provider("nvidia")
        except ValueError as e:
            assert "NVIDIA_API_KEY" in str(e)
        else:
            raise AssertionError("debía fallar sin clave")


def test_nvidia_usa_su_base_url_y_modelo_del_env():
    with env(NVIDIA_API_KEY="clave-falsa", NVIDIA_MODEL="org/modelo-x", NVIDIA_REASONING_EFFORT=None):
        provider = get_provider("nvidia")
    assert isinstance(provider, OpenAICompatibleProvider)
    assert provider.name == "nvidia"
    assert provider.model == "org/modelo-x"
    assert str(provider.client.base_url).rstrip("/") == SPECS["nvidia"].base_url


def test_model_pisa_al_env():
    with env(NVIDIA_API_KEY="clave-falsa", NVIDIA_MODEL="org/modelo-x"):
        assert get_provider("nvidia", model="otro/modelo").model == "otro/modelo"


def test_structured_invalido_da_error():
    with env(NVIDIA_API_KEY="clave-falsa", NVIDIA_STRUCTURED="xml"):
        try:
            get_provider("nvidia")
        except ValueError as e:
            assert "NVIDIA_STRUCTURED" in str(e)
        else:
            raise AssertionError("debía fallar con un modo inválido")


# --- Parámetros de la llamada --------------------------------------------------

def test_groq_mantiene_la_llamada_del_proyecto_base():
    # Mismos parámetros que el GroqProvider original: reasoning_effort=low y json_schema estricto.
    with env(GROQ_API_KEY="clave-falsa", GROQ_REASONING_EFFORT=None, GROQ_STRUCTURED=None, GROQ_MODEL=None):
        provider = get_provider("groq")
    req = provider.build_request("sys", "user", output_json_schema())
    assert req["model"] == "openai/gpt-oss-120b"
    assert req["reasoning_effort"] == "low"
    assert req["response_format"]["type"] == "json_schema"
    assert req["response_format"]["json_schema"]["strict"] is True
    assert req["temperature"] == 0.0


def test_nvidia_sin_reasoning_no_manda_el_parametro():
    with env(NVIDIA_API_KEY="clave-falsa", NVIDIA_REASONING_EFFORT=""):
        req = get_provider("nvidia").build_request("sys", "user", {})
    assert "reasoning_effort" not in req


def test_modos_de_salida_estructurada():
    with env(NVIDIA_API_KEY="clave-falsa", NVIDIA_STRUCTURED="json_object"):
        assert get_provider("nvidia").build_request("s", "u", {})["response_format"] == {"type": "json_object"}
    with env(NVIDIA_API_KEY="clave-falsa", NVIDIA_STRUCTURED="off"):
        assert "response_format" not in get_provider("nvidia").build_request("s", "u", {})


def test_complete_lee_texto_y_tokens():
    with env(NVIDIA_API_KEY="clave-falsa"):
        provider = get_provider("nvidia")
    provider.client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(
        create=lambda **kw: fake_completion(VALID))))
    result = provider.complete("sys", "user")
    assert result.text == VALID and result.provider == "nvidia"
    assert (result.prompt_tokens, result.completion_tokens, result.stop_reason) == (100, 50, "stop")


# --- Costo y errores de API ------------------------------------------------------

def test_costo_nvidia_es_cero_aunque_el_modelo_no_este_en_la_tabla():
    assert estimate_cost_usd("modelo/desconocido", 1000, 1000, "nvidia") == 0.0
    assert estimate_cost_usd("modelo/desconocido", 1000, 1000, "groq") is None  # sin dato, no inventa


def _fake_provider(create):
    with env(NVIDIA_API_KEY="clave-falsa", NVIDIA_MODEL="org/modelo-x"):
        provider = get_provider("nvidia")
    provider.client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    return provider


def test_error_429_queda_registrado_con_su_codigo():
    def create(**kw):
        raise rate_limit_error()
    provider = _fake_provider(create)
    original = service.get_provider
    service.get_provider = lambda name, model=None: provider
    try:
        with temp_metrics() as path:
            try:
                service.triage("no puedo entrar a mi cuenta", "nvidia", use_cache=False, moderation="off")
            except service.TriageError as e:
                assert "RateLimitError" in str(e)
            else:
                raise AssertionError("debía fallar con 429")
            lines = read_lines(path)
    finally:
        service.get_provider = original
    assert [l["event"] for l in lines] == ["error_api"]
    assert lines[0]["status_code"] == 429 and lines[0]["provider"] == "nvidia"


def test_triage_completo_con_nvidia_falso():
    provider = _fake_provider(lambda **kw: fake_completion(VALID))
    original = service.get_provider
    service.get_provider = lambda name, model=None: provider
    try:
        with temp_metrics() as path:
            result = service.triage("me cobraron dos veces", "nvidia", use_cache=False, moderation="off")
            lines = read_lines(path)
    finally:
        service.get_provider = original
    assert result.response.category == "billing"
    assert len(lines) == 1 and lines[0]["event"] == "llm" and lines[0]["cost_usd"] == 0.0


# --- Versión gratuita: Anthropic no se usa por defecto -------------------------------

def test_moderador_por_defecto_es_nvidia():
    with env(MODERATOR_PROVIDER=None):
        assert service.moderator_provider() == "nvidia"
        assert "anthropic" not in service.calls_preview(["groq"], "both")


def test_runners_por_defecto_solo_proveedores_gratis():
    assert set(FREE_PROVIDERS) == {"groq", "nvidia"}


def test_env_example_no_tiene_anthropic():
    text = (ROOT / ".env.example").read_text(encoding="utf-8")
    assert "ANTHROPIC" not in text.upper().replace("ANTHROPIC NO SE USA", "")
    assert "MODERATOR_PROVIDER=nvidia" in text


# --- Aviso previo de los runners ---------------------------------------------------

def test_aviso_marca_el_moderador_pago():
    with env(MODERATOR_PROVIDER="anthropic"):
        text = service.calls_preview(["nvidia"], "both")
    assert "nvidia: responde (gratis)" in text and "anthropic: modera (PAGO)" in text


def test_aviso_sin_moderacion_no_lista_moderador():
    with env(MODERATOR_PROVIDER="anthropic"):
        text = service.calls_preview(["nvidia"], "off")
    assert "anthropic" not in text


def main() -> int:
    tests = [(name, fn) for name, fn in globals().items() if name.startswith("test_") and callable(fn)]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"ok    {name}")
        except Exception as e:  # noqa: BLE001  (un error inesperado también es una falla)
            failed += 1
            print(f"FALLA {name}: {type(e).__name__}: {e}")
    print(f"\n{len(tests) - failed}/{len(tests)} tests ok")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main())
