# Plan: versión gratuita del triage (NVIDIA + Jev)

Base: [saragismondi/pi-m1-ia-engineer](https://github.com/saragismondi/pi-m1-ia-engineer), commit `0d78a0a`
(historial completo conservado). Objetivo: correr el proyecto integrador sin proveedores pagos y medir si
Jev (TypeSafe AI) mejora las decisiones de categoría y moderación.

Formato: cada dato dice de dónde sale y si está **verificado** o no.

## 1. Cuántas llamadas hizo el proyecto base (verificado)

Fuente: `data/metrics.jsonl` del proyecto base (1.186 líneas, 1.184 llamadas a un LLM), todo el
07/11/2026 entre las 17:36 y las 23:51 UTC.

| Proveedor / modelo | Llamadas | Tokens (entrada + salida) | Costo |
|---|---|---|---|
| Anthropic / claude-haiku-5-5 | 766 (264 moderador de entrada + 205 de salida) | 766.472 | 0,112 USD |
| Groq / openai/gpt-oss-20b | 315 | 262.627 | 0 |
| Groq / openai/gpt-oss-120b | 103 | 110.802 | 0 |
| **Total** | **1.184** | **~1,14 M** | **0,112 USD** |

- Pico por minuto: 42 llamadas (Anthropic) y 27 (Groq). Pico de tokens por minuto: 67.151.
- Por ticket: hasta 3 llamadas (respuesta + moderador de entrada + moderador de salida).
- Corrida de evals, un proveedor, v1 y v2: 40 casos × 2 prompts × hasta 3 = **~240 llamadas**.
- Corrida adversarial, un proveedor: 10 ataques × 2 prompts × 2 modos × hasta 3 = **≤120 llamadas**.
- Antecedente: Groq cortó con 429 por el cupo diario de 200.000 tokens (`evals/results/README.md`, "Lección 2").

## 2. Límites de NVIDIA build.nvidia.com

- **Verificado (página de modelos, 07/11/2026):** hay modelos de chat (`gpt-oss-20b`, `deepseek-v4.1-flash`,
  `gemma-4-31b-it`, `glm-5-3-flash`, `Mistral Large 4`, `nemotron-3.5-lightning-30b-a3b`, `kimi-k3.1-pro`,
  entre otros) y de seguridad (`nemoguard-jailbreak-detect`, `llama-guard-4-12b`,
  `nemotron-3.5-content-safety`). `gpt-oss-120b` no aparece.
- **No verificado:** no hay un límite oficial publicado. Usuarios del foro de NVIDIA reportan **40 llamadas por
  minuto** y errores 429; una respuesta de moderador citada en el foro dice que el límite depende del modelo,
  el caso de uso y el tráfico general, y que no hay forma oficial de subirlo. Un blog dice que ya no hay
  créditos ni cupo diario publicado.
- **Consecuencia:** el pico medido (42/min) supera 40. Con `--sleep 1.6` el runner queda en ~37/min.

## 3. Jev (TypeSafe AI)

- **Verificado (docs.typesafe.ai, jevmodel.io/docs):** no genera texto. Responde preguntas tipadas
  (`choice`, `score`, `noul` = verdadero/falso) con probabilidades. Sirve para `category` y moderación;
  **no** para `answer` ni `actions`.
- Límites gratis: jevmodel.io da 1.000 créditos y 100 decisiones por día (1 crédito por decisión).
  typesafe.pro: anónimo y gratis con "fair use" sin números.
- **Ojo:** jevmodel.io, typesafe.pro y jevai.net no son de TypeSafe (los dos últimos lo dicen; jevmodel.io no
  nombra a su operador). Lo que se mande lo procesan terceros: no usar datos reales de clientes.
- **Sin verificar:** que sus probabilidades estén calibradas. Se mide en la fase 5.

## 4. Fases

| Fase | Qué | Llamadas | Estado |
|---|---|---|---|
| 0 | Clave de NVIDIA (la crea Sara en build.nvidia.com) y, opcional, de Jev | 0 | **Pendiente (Sara)** |
| 1 | Prueba de humo: 1 llamada por modelo candidato (`scripts/smoke_openai_compatible.py`) | ~7 | Script listo |
| 2 | Proveedor genérico OpenAI-compatible (Groq y NVIDIA), `--moderation` en los runners, aviso de proveedores pagos, registro de errores de API (`error_api` con `status_code`) | 0 | **Hecho** (tests sin red) |
| 3 | Evals con los 2 mejores modelos, v1, `--moderation off`, `--sleep 1.6` | ~80 | Pendiente |
| 4 | Moderador en NVIDIA en lugar de Haiku; evals normales y adversariales | ~200 | Pendiente |
| 5 | Jev: categoría con probabilidades en los 40 casos (acierto y calibración vs `confidence` del LLM) y moderación en los 10 ataques | ~50 decisiones | Pendiente |
| 6 | Decisión de Sara con la tabla de resultados | — | — |

## 5. Pregunta abierta para Henry

La consigna (`template-PI-M1/README.md`, línea 33) dice: "Al menos dos proveedores configurables mediante una
abstracción común". Con solo NVIDIA serían dos modelos detrás de una API. No sabemos si eso cuenta como dos
proveedores. NVIDIA + Groq cumple seguro y es gratis.

## Comandos de la fase 1 (cuando esté la clave)

```bash
python scripts/smoke_openai_compatible.py --provider nvidia --list-models --filter gpt-oss
python scripts/smoke_openai_compatible.py --provider nvidia --models openai/gpt-oss-20b
```

## Fuentes

- https://build.nvidia.com/models (y `?page=2`)
- https://forums.developer.nvidia.com/t/api-credit-rate-limit-increase-request-for-build-nvidia-com-free-tier/382934
- https://forums.developer.nvidia.com/t/request-for-nvidia-build-api-rate-limit-increase-40-rpm-200-rpm/378450
- https://decodethefuture.org/en/nvidia-nim-api-pricing-limits-guide/
- https://docs.typesafe.ai/ · https://jevmodel.io/docs · https://typesafe.pro/ · https://jevai.net/es/
- https://defapi.org/model-group/typesafe/jev?model=jev-1.13
