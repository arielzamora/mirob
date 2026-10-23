"""Ejemplos de uso de la capa /rag, para probar a mano desde la terminal.

Correr desde backend/ con el venv activado:
    python example_usage.py
"""

import pandas as pd

from app.chunk_format import extract_answer, extract_header
from app.rag.embeddings import fit_vectorizer
from app.rag.humanize import humanizar
from app.rag.ingest import build_corpus
from app.rag.retrieve import top_k

# La carpeta se decide en un único lugar (reindex.py): data/ si tiene tu
# contenido real, o data/sample/ (perfil ficticio) si no. Así el script y
# la API nunca se desincronizan.
from app.reindex import resolver_carpeta_contenido


def mostrar_resultado(pregunta: str, row: pd.Series, modo: str) -> None:
    """Imprime el resultado top-1 de una pregunta en modo "raw" o "humanized".

    - raw: el chunk recuperado tal cual devuelve retrieve.py (sin cambios).
    - humanized: extrae la respuesta del chunk y la pasa por humanizar().
    """
    print(f"[{modo}] archivo: {row['source']} | sección: {extract_header(row['text'])} | score: {row['similarity']:.3f}")
    print("-" * 80)

    if modo == "raw":
        print(row["text"])
    elif modo == "humanized":
        respuesta = extract_answer(row["text"])
        print(humanizar(pregunta, respuesta, row["similarity"]))
    else:
        raise ValueError(f"Modo no soportado: {modo}")


def main() -> None:
    # Paso 1 — Ingesta: lee toda la carpeta de contenido y arma un chunk por fila del DataFrame.
    corpus = build_corpus(resolver_carpeta_contenido())
    print(f"Corpus: {len(corpus)} chunks de {corpus['source'].nunique()} archivo(s)\n")
    print(corpus[["chunk_id", "source"]])

    # Paso 2 — Embeddings: ajusta el TF-IDF sobre el corpus completo.
    vectorizer, matrix = fit_vectorizer(corpus)

    preguntas = [
        "¿Qué certificaciones tiene Ariel?",
        "¿Cómo manejás el estrés en momentos de alta presión?",
        "¿Para qué sirve MiRob?",
        # Fuera de tema a propósito: nada en el contenido habla de esto, así que
        # el score del top-1 queda bajo (~0.18) y muestra la rama de baja
        # confianza de humanizar() (umbral: UMBRAL_BAJA_CONFIANZA = 0.25).
        "¿Cuál es la capital de Mongolia?",
    ]

    for pregunta in preguntas:
        print("\n" + "=" * 80)
        print(f"PREGUNTA: {pregunta}")
        print("=" * 80)

        # Todavía sin combinar top-2 chunks: se compara raw vs humanized
        # sobre el top-1 únicamente.
        row = top_k(pregunta, vectorizer, matrix, corpus, k=1).iloc[0]

        print()
        mostrar_resultado(pregunta, row, modo="raw")
        print()
        mostrar_resultado(pregunta, row, modo="humanized")


if __name__ == "__main__":
    main()
