"""Tests de la ingesta: chunking de .md/.txt y armado del DataFrame de chunks.

Son tests unitarios puros: no hay red ni base de datos, solo texto y archivos
temporales. Un test en pytest es una función que empieza con `test_` y usa
`assert` (en xUnit sería un método con [Fact] y Assert.Equal).
"""

from pathlib import Path

import pandas as pd
import pytest

from app.rag.ingest import build_corpus, chunk_file, chunk_markdown, chunk_text


# --- chunk_markdown -------------------------------------------------------


def test_markdown_corta_por_headers_y_los_conserva() -> None:
    texto = "# Titulo\n\n## A\ntexto a\n\n## B\ntexto b"
    # El lookahead de la regex corta ANTES de cada "## " sin comerse el header.
    assert chunk_markdown(texto) == ["# Titulo", "## A\ntexto a", "## B\ntexto b"]


def test_markdown_sin_headers_es_un_unico_chunk() -> None:
    assert chunk_markdown("solo texto\nsin headers") == ["solo texto\nsin headers"]


def test_markdown_vacio_no_genera_chunks() -> None:
    # Un texto con solo espacios/saltos no debe producir chunks vacíos.
    assert chunk_markdown("  \n\n  ") == []


def test_markdown_no_corta_en_headers_de_nivel_3() -> None:
    # "###" no es un separador: solo se corta por "## " (nivel 2).
    assert chunk_markdown("## A\n### sub\ntexto") == ["## A\n### sub\ntexto"]


# --- chunk_text -----------------------------------------------------------


def test_txt_corta_por_linea_en_blanco() -> None:
    assert chunk_text("uno\ndos\n\ntres\n\n\ncuatro") == ["uno\ndos", "tres", "cuatro"]


def test_txt_vacio_no_genera_chunks() -> None:
    assert chunk_text("\n\n") == []


# --- chunk_file -----------------------------------------------------------


def test_chunk_file_elige_estrategia_por_extension(tmp_path: Path) -> None:
    md = tmp_path / "a.MD"  # mayúsculas a propósito: la extensión se normaliza
    md.write_text("## A\nx\n\n## B\ny", encoding="utf-8")
    txt = tmp_path / "b.txt"
    txt.write_text("p1\n\np2", encoding="utf-8")

    assert chunk_file(md) == ["## A\nx", "## B\ny"]
    assert chunk_file(txt) == ["p1", "p2"]


def test_chunk_file_rechaza_extensiones_no_soportadas(tmp_path: Path) -> None:
    pdf = tmp_path / "cv.pdf"
    pdf.write_text("no importa", encoding="utf-8")
    # pytest.raises es el Assert.Throws<ValueError>(...) de xUnit.
    with pytest.raises(ValueError, match="Extensión no soportada"):
        chunk_file(pdf)


# --- build_corpus ---------------------------------------------------------


def test_build_corpus_ids_correlativos_y_orden_estable(tmp_path: Path) -> None:
    # Se crean fuera de orden a propósito: el resultado debe salir ordenado por nombre.
    (tmp_path / "b.txt").write_text("b1\n\nb2", encoding="utf-8")
    (tmp_path / "a.md").write_text("## A1\nx\n\n## A2\ny", encoding="utf-8")

    corpus = build_corpus(tmp_path)

    assert list(corpus.columns) == ["chunk_id", "source", "text"]
    assert corpus["chunk_id"].tolist() == [0, 1, 2, 3]
    assert corpus["source"].tolist() == ["a.md", "a.md", "b.txt", "b.txt"]


def test_build_corpus_ignora_otros_formatos_y_subcarpetas(tmp_path: Path) -> None:
    (tmp_path / "ok.txt").write_text("hola", encoding="utf-8")
    (tmp_path / "foto.png").write_bytes(b"\x89PNG")
    (tmp_path / "subcarpeta").mkdir()

    assert build_corpus(tmp_path)["source"].tolist() == ["ok.txt"]


def test_build_corpus_en_carpeta_vacia_conserva_las_columnas(tmp_path: Path) -> None:
    corpus = build_corpus(tmp_path)
    assert corpus.empty
    assert list(corpus.columns) == ["chunk_id", "source", "text"]


def test_corpus_ficticio_tiene_los_cuatro_archivos(corpus: pd.DataFrame) -> None:
    # `corpus` llega por inyección desde conftest.py (fixture del mismo nombre).
    assert set(corpus["source"]) == {
        "habilidades-blandas.md",
        "objetivo.txt",
        "personal.md",
        "tecnica.md",
    }
    # Todos los chunks tienen texto y los ids no se repiten.
    assert corpus["text"].str.strip().ne("").all()
    assert corpus["chunk_id"].is_unique
