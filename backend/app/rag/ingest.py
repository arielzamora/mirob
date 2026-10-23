"""Ingesta de contenido: lee .txt/.md de una carpeta y arma un DataFrame de chunks."""

import re
from pathlib import Path

import pandas as pd

# Regex precompiladas a nivel de módulo (se compilan una sola vez, no en cada
# llamada a la función). re.MULTILINE hace que "^" matchee el inicio de
# CUALQUIER línea del texto, no solo el inicio del string completo.

# (?=^## ) es un "lookahead": encuentra la posición justo ANTES de cada línea
# que empieza con "## ", pero no consume esos caracteres. Por eso, al usarla
# con .split(), el separador queda pegado al chunk siguiente en vez de
# desaparecer — así el header "## Certificaciones" no se pierde y queda
# como parte del texto del chunk.
_HEADER_PATTERN = re.compile(r"(?=^## )", re.MULTILINE)

# \n\s*\n matchea un salto de línea, seguido de cero o más espacios/blancos
# (incluye otro \n), seguido de otro salto de línea. En criollo: "una línea
# en blanco entre dos párrafos", que es como pandas/markdown separan bloques.
_BLANK_LINE_PATTERN = re.compile(r"\n\s*\n")


def chunk_markdown(text: str) -> list[str]:
    """Separa un .md en chunks por header de nivel 2 (##).

    Si el archivo no tiene headers, se devuelve como un único chunk.
    """
    # .split() con un patrón basado en lookahead corta el texto en cada
    # posición donde matchea el patrón, sin eliminar nada del contenido.
    # Ejemplo: "# Titulo\n\n## A\ntexto a\n\n## B\ntexto b"
    #   -> ["# Titulo\n\n", "## A\ntexto a\n\n", "## B\ntexto b"]
    parts = _HEADER_PATTERN.split(text)

    # Descartamos partes vacías (o que sean solo espacios) y recortamos
    # espacios sobrantes al principio/final de cada chunk con .strip().
    # Esto es una "list comprehension": por cada `part` en `parts`, si
    # `part.strip()` no es un string vacío (falsy), lo agregamos ya "stripeado".
    return [part.strip() for part in parts if part.strip()]


def chunk_text(text: str) -> list[str]:
    """Separa un .txt en chunks por párrafo (línea en blanco como separador)."""
    # re.split() con un patrón normal (sin lookahead) SÍ consume el separador:
    # la línea en blanco desaparece y quedan los párrafos sueltos en la lista.
    parts = _BLANK_LINE_PATTERN.split(text)
    return [part.strip() for part in parts if part.strip()]


def chunk_file(path: Path) -> list[str]:
    """Elige la estrategia de chunking según la extensión del archivo."""
    # Path.read_text() abre, lee y cierra el archivo en una sola línea.
    # encoding="utf-8" explícito porque en Windows el default puede ser
    # cp1252/latin-1, lo que rompe tildes y ñ.
    text = path.read_text(encoding="utf-8")
    suffix = path.suffix.lower()  # ".MD" y ".md" deben tratarse igual
    if suffix == ".md":
        return chunk_markdown(text)
    if suffix == ".txt":
        return chunk_text(text)
    # Fail fast: si en algún momento se cuela un formato no soportado,
    # mejor un error explícito acá que un chunk mal formado más adelante.
    raise ValueError(f"Extensión no soportada: {path.suffix}")


def build_corpus(content_dir: str | Path) -> pd.DataFrame:
    """Recorre `content_dir` y arma un DataFrame con un chunk por fila.

    Columnas: chunk_id (int), source (nombre de archivo), text (contenido del chunk).
    """
    # Acepta tanto un string como un Path; Path(...) sobre un Path existente
    # no rompe nada (es una operación idempotente).
    content_dir = Path(content_dir)

    rows = []  # lista de dicts; al final se convierte en DataFrame de una
    chunk_id = 0  # id incremental, único en TODO el corpus (no por archivo)

    # sorted(...) asegura un orden determinístico entre corridas (el orden
    # del filesystem no está garantizado) — importante para que chunk_id
    # sea reproducible y los tests puedan hacer asserts exactos.
    for path in sorted(content_dir.glob("*")):
        # glob("*") también devuelve subcarpetas y otros archivos; filtramos
        # para quedarnos solo con .md/.txt.
        if path.suffix.lower() not in (".md", ".txt"):
            continue
        for chunk in chunk_file(path):
            rows.append({"chunk_id": chunk_id, "source": path.name, "text": chunk})
            chunk_id += 1

    # pd.DataFrame(rows) ya arma las columnas solo, pero si `rows` está
    # vacío (carpeta sin contenido válido) no sabría qué columnas usar.
    # Pasar columns=[...] explícito evita ese caso borde.
    return pd.DataFrame(rows, columns=["chunk_id", "source", "text"])
