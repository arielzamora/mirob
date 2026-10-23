"""Tests de chunk_format: extracción del header y de la respuesta "R:" de un chunk."""

from app.chunk_format import extract_answer, extract_header


def test_extract_header_devuelve_la_primera_linea_si_es_header() -> None:
    assert extract_header("## Certificaciones\nP: x\nR: y") == "## Certificaciones"


def test_extract_header_avisa_cuando_no_hay_header() -> None:
    # Los chunks de .txt no empiezan con "#".
    assert "sin header" in extract_header("P: x\nR: y")


def test_extract_answer_toma_solo_el_texto_de_la_respuesta() -> None:
    chunk = "## Tema\nP: ¿Qué hace?\nR: Hace algo útil."
    assert extract_answer(chunk) == "Hace algo útil."


def test_extract_answer_une_respuestas_de_varias_lineas() -> None:
    chunk = "P: ¿Qué?\nR: primera línea\nsegunda línea\n\nP: otra"
    # Corta en la línea en blanco: lo que sigue ya no es parte de esa respuesta.
    assert extract_answer(chunk) == "primera línea segunda línea"


def test_extract_answer_con_varios_pares_devuelve_solo_el_primero() -> None:
    chunk = "P: uno\nR: respuesta uno\nP: dos\nR: respuesta dos"
    assert extract_answer(chunk) == "respuesta uno"


def test_extract_answer_sin_formato_pr_devuelve_el_chunk_entero() -> None:
    assert extract_answer("  texto suelto sin formato  ") == "texto suelto sin formato"
