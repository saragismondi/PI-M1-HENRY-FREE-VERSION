"""Guardrails: controles deterministas (sin LLM, costo 0) antes y después del modelo.

Entrada (check_input): rechaza consultas vacías o demasiado largas y detecta intentos
de prompt injection con patrones de texto. Si detecta uno, el servicio NO llama al LLM.

Salida (check_output): revisa el "answer" del modelo antes de mostrarlo:
  - promesas (reintegros, correcciones, plazos) que solo puede decidir un agente humano;
  - fragmentos del system prompt (fuga de instrucciones).
Si encuentra algo, el servicio reemplaza el "answer" por uno seguro.

Limitación conocida: son patrones de texto. Un ataque reformulado puede pasar el guardrail
de entrada; por eso el prompt también trata el ticket como dato y existe el control de salida.
"""
import re
import unicodedata

MAX_QUERY_CHARS = 2000  # la consulta más larga del dataset mide 116 caracteres

# Se aplican sobre el texto normalizado: minúsculas y sin tildes.
INJECTION_PATTERNS = {
    "ignorar_instrucciones": r"\b(ignor|olvid|descart|saltea|omit)\w*\s+(\w+\s+){0,4}(instrucciones|reglas|indicaciones|prompt)",
    "ignore_instructions_en": r"\b(ignore|disregard|forget)\s+(\w+\s+){0,3}(instructions|rules|prompt)",
    "pedir_prompt": r"\b(revela|mostra|mostrame|muestra|decime|dime|imprimi|imprime|repeti|repite|copia|escribi)\w*\s+(\w+\s+){0,5}(instrucciones|prompt|reglas|configuracion)",
    "mencion_system_prompt": r"\b(system prompt|prompt del sistema|prompt de sistema|instrucciones del sistema|instrucciones internas)",
    "cambio_de_rol": r"\b(a partir de ahora|desde ahora)\s+(sos|eres|actua|responde|tenes que)|\b(actua|comportate)\s+como\b|\byou are now\b",
    "modo_sin_reglas": r"\b(modo desarrollador|developer mode|jailbreak|sin restricciones|sin filtros|sin reglas)\b",
    "etiquetas_falsas": r"</?\s*(ticket|system|instrucciones)\s*>|\[/?(system|inst)\]|^\s*(system|assistant)\s*:",
}

PROMISE_PATTERNS = {
    "reintegro": r"\b(te|le|les)\s+(vamos a |va a |van a )?(devolver|reintegrar|reembolsar)\w*|\b(devolveremos|reintegraremos|reembolsaremos)\b|\b(vamos a|va a|van a)\s+(devolver|reintegrar|reembolsar|anular el cobro)",
    "correccion": r"\b(corregiremos|solucionaremos|resolveremos|arreglaremos|repararemos)\b|\b(vamos a|va a|van a)\s+(corregir|solucionar|resolver|arreglar|reparar)\b",
    "garantia": r"\b(te|le)\s+(garantizo|garantizamos|aseguro|aseguramos)\b|\bgarantizad[oa]\b",
    "plazo": r"\ben\s+(las\s+)?(proximas\s+)?\d+\s*(minutos|horas|hs|dias)\b|\bhoy mismo\b|\bde inmediato\b",
}

SAFE_ANSWER = "Gracias por escribirnos. Un agente de soporte va a revisar tu caso y te va a contactar."
LEAK_WINDOW = 8  # palabras seguidas del system prompt que cuentan como fuga


def normalize(text: str) -> str:
    """Minúsculas y sin tildes, para que 'Ignorá' y 'ignora' coincidan con el mismo patrón."""
    text = unicodedata.normalize("NFKD", text.lower())
    return "".join(c for c in text if not unicodedata.combining(c))


def check_input(query: str) -> tuple[str | None, str | None]:
    """Devuelve (error, injection).

    error: la consulta no se puede procesar (vacía o demasiado larga).
    injection: nombre del patrón detectado, o None si no detectó nada.
    """
    if not query.strip():
        return "La consulta está vacía.", None
    if len(query) > MAX_QUERY_CHARS:
        return f"La consulta supera el máximo de {MAX_QUERY_CHARS} caracteres ({len(query)}).", None
    text = normalize(query)
    for name, pattern in INJECTION_PATTERNS.items():
        if re.search(pattern, text, flags=re.MULTILINE):
            return None, name
    return None, None


def _words(text: str) -> list[str]:
    return re.findall(r"\w+", normalize(text))


def _prompt_windows(system_prompt: str) -> set[tuple[str, ...]]:
    """Secuencias de LEAK_WINDOW palabras del system prompt.

    Se excluyen las líneas de ejemplos few-shot (Ticket: ... / {"answer": ...}): son respuestas
    modelo y una respuesta real puede parecerse a ellas sin que sea una fuga.
    """
    lines = [l for l in system_prompt.splitlines()
             if not l.strip().startswith(("Ticket:", '{"answer"'))]
    words = _words("\n".join(lines))
    return {tuple(words[i : i + LEAK_WINDOW]) for i in range(len(words) - LEAK_WINDOW + 1)}


def check_output(answer: str, system_prompt: str) -> list[str]:
    """Devuelve la lista de problemas encontrados en el answer (vacía = OK)."""
    text = normalize(answer)
    flags = [f"promesa:{name}" for name, pattern in PROMISE_PATTERNS.items() if re.search(pattern, text)]
    words = _words(answer)
    windows = _prompt_windows(system_prompt)
    if any(tuple(words[i : i + LEAK_WINDOW]) in windows for i in range(len(words) - LEAK_WINDOW + 1)):
        flags.append("fuga_system_prompt")
    return flags
