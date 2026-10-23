"""Utilidades para extraer partes legibles de un chunk con formato P/R.

No es parte de /rag (no vectoriza ni recupera nada) — es una capa de
presentación chica, compartida entre example_usage.py y la API, para no
duplicar el parseo del chunk en los dos lugares.
"""


def extract_header(chunk_text: str) -> str:
    """Primera línea del chunk si es un header markdown; si no, indica que no tiene.

    Los chunks de .md conservan su header ("## Algo") como primera línea
    (ver app/rag/ingest.py: chunk_markdown). Los chunks de .txt no tienen
    esa noción de header.
    """
    first_line = chunk_text.splitlines()[0]
    return first_line if first_line.startswith("#") else "(sin header — chunk de .txt)"


def extract_answer(chunk_text: str) -> str:
    """Extrae el texto de la primera respuesta ("R:") de un chunk.

    El contenido está escrito como pares "P: ... / R: ...". Buscamos la
    primera línea que empieza con "R:" y juntamos todo hasta la línea en
    blanco o el siguiente "P:" (algunos chunks tienen más de un par P/R).

    Si el chunk no sigue el formato P/R, se devuelve el chunk entero como
    fallback (no debería pasar con el contenido actual, pero por las dudas).
    """
    lines = chunk_text.splitlines()
    answer_lines = []
    capturing = False

    for line in lines:
        stripped = line.strip()
        if stripped.startswith("R:"):
            capturing = True
            answer_lines.append(stripped[len("R:"):].strip())
            continue
        if capturing:
            if not stripped or stripped.startswith("P:"):
                break
            answer_lines.append(stripped)

    if answer_lines:
        return " ".join(answer_lines)
    return chunk_text.strip()
