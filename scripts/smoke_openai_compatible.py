"""Fase 1: prueba de humo de modelos en un proveedor compatible con OpenAI (NVIDIA, Groq).

Antes de correr evals (cientos de llamadas) se hace UNA llamada por modelo para ver:
  - si el modelo existe con ese nombre en la API;
  - si acepta la salida estructurada pedida (json_schema / json_object / off);
  - si la respuesta respeta el contrato (Pydantic), cuántos tokens usa y cuánto tarda;
  - si devuelve un error de API (por ejemplo 404 = no existe, 400 = no acepta el formato, 429 = límite).

Uso:
    python scripts/smoke_openai_compatible.py --provider nvidia --list-models          # no gasta inferencia
    python scripts/smoke_openai_compatible.py --provider nvidia --models openai/gpt-oss-20b otro/modelo
    python scripts/smoke_openai_compatible.py --provider nvidia --models X --structured json_object

Cada llamada queda en data/metrics.jsonl (event "llm" o "error_api"), igual que el resto del proyecto.
No usa moderador ni caché: es una llamada por modelo y nada más.
"""
import argparse
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))  # permite correrlo como `python scripts/smoke_openai_compatible.py`

from dotenv import load_dotenv  # noqa: E402
from pydantic import ValidationError  # noqa: E402

from src.metrics import log_api_error, log_request  # noqa: E402
from src.prompts import load_prompt  # noqa: E402
from src.providers import API_ERRORS, SPECS, STRUCTURED_MODES, get_provider  # noqa: E402
from src.schemas import TriageResponse, extract_json  # noqa: E402

# Un ticket claro del dataset (bil-01 o similar): la prueba mide el formato, no la dificultad.
TICKET = "me cobraron dos veces la suscripción de este mes"


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    load_dotenv(ROOT / ".env")
    parser = argparse.ArgumentParser(description="Una llamada por modelo para validar formato y límites.")
    parser.add_argument("--provider", choices=list(SPECS), default="nvidia")
    parser.add_argument("--models", nargs="+", default=None, help="IDs de modelo tal como los lista la API.")
    parser.add_argument("--list-models", action="store_true", help="Solo lista los modelos (GET /models).")
    parser.add_argument("--filter", default="", help="Con --list-models: texto que debe contener el ID.")
    parser.add_argument("--structured", choices=STRUCTURED_MODES, default=None,
                        help="Pisa <PROVEEDOR>_STRUCTURED del .env para esta prueba.")
    parser.add_argument("--prompt", default="v1")
    parser.add_argument("--sleep", type=float, default=2.0, help="Espera entre modelos (segundos).")
    args = parser.parse_args()

    if args.structured:
        os.environ[SPECS[args.provider].structured_env] = args.structured

    try:
        get_provider(args.provider)  # falla temprano y claro si falta la clave
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    if args.list_models:
        provider = get_provider(args.provider)
        ids = sorted(m.id for m in provider.client.models.list())
        ids = [i for i in ids if args.filter.lower() in i.lower()]
        print(f"{len(ids)} modelos en {provider.base_url}" + (f" que contienen {args.filter!r}" if args.filter else ""))
        for model_id in ids:
            print(" ", model_id)
        return 0

    if not args.models:
        parser.error("Pasá --models (o usá --list-models para ver los IDs).")

    prompt = load_prompt(args.prompt)
    rows = []
    for i, model in enumerate(args.models):
        provider = get_provider(args.provider, model=model)
        row = {"model": model, "structured": provider.structured, "resultado": "", "tokens": "-",
               "latencia_s": "-", "stop": "-"}
        try:
            result = provider.complete(prompt.system, prompt.render_user(TICKET))
        except API_ERRORS as e:
            log_api_error(provider.name, model, f"smoke_{args.prompt}", e)
            row["resultado"] = f"ERROR {getattr(e, 'status_code', None)}: {str(e)[:120]}"
        else:
            row.update(tokens=f"{result.prompt_tokens}+{result.completion_tokens}",
                       latencia_s=f"{result.latency_s:.2f}", stop=result.stop_reason)
            try:
                response = TriageResponse.model_validate(extract_json(result.text))
                log_request(result, f"smoke_{args.prompt}", 1, valid=True, category=response.category)
                row["resultado"] = f"OK category={response.category} confidence={response.confidence}"
            except (ValueError, ValidationError) as e:
                log_request(result, f"smoke_{args.prompt}", 1, valid=False, category=None)
                row["resultado"] = f"CONTRATO INVÁLIDO: {str(e)[:80]} | texto: {result.text[:80]!r}"
        rows.append(row)
        print(f"- {model} [{row['structured']}] {row['resultado']} tokens={row['tokens']} "
              f"latencia={row['latencia_s']} stop={row['stop']}")
        if args.sleep and i < len(args.models) - 1:
            time.sleep(args.sleep)

    cols = list(rows[0])
    print("\n| " + " | ".join(cols) + " |\n|" + "---|" * len(cols))
    for r in rows:
        print("| " + " | ".join(str(r[c]).replace("|", "/") for c in cols) + " |")
    return 0


if __name__ == "__main__":
    sys.exit(main())
