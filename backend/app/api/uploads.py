"""Validación de los archivos que se suben a POST /admin/content.

Funciones puras (sin FastAPI ni disco) para poder testearlas fácil. La regla de
oro: el nombre que manda el cliente es un DATO NO CONFIABLE. Nunca se arma una
ruta con él sin validarlo antes (path traversal: "../../x.md").
"""

import re
from pathlib import Path

# Extensiones que puede ingerir ingest.chunk_file (ver app/rag/ingest.py).
EXTENSIONES_PERMITIDAS = (".md", ".txt")

# Tope de tamaño de un archivo subido: 1 MB alcanza de sobra para texto plano
# y evita que alguien llene la memoria o el disco con un archivo enorme.
TAMANO_MAXIMO_BYTES = 1_000_000

# Lista blanca de caracteres (en vez de lista negra de "caracteres peligrosos"):
#   \w            letra, número o "_" (incluye tildes y ñ, porque Python usa Unicode)
#   [\w\-. ()]    además se permiten guion, punto, espacio y paréntesis
#   {0,99}        hasta 100 caracteres en total
# El primer carácter tiene que ser \w: así no puede empezar con "." (".md", "..x").
# Notá que "/" y "\" NO están permitidos, así que no hay forma de salir de la carpeta.
_NOMBRE_VALIDO = re.compile(r"\w[\w\-. ()]{0,99}")


def validar_nombre_archivo(nombre: str | None) -> str:
    """Devuelve el nombre si es seguro; si no, lanza ValueError con el motivo.

    Se rechaza (en vez de "limpiar" el nombre en silencio) para que quien sube
    el archivo vea el problema y no termine con un archivo guardado con otro nombre.
    """
    # fullmatch exige que TODO el texto cumpla el patrón (como un ^...$ completo).
    if not nombre or not _NOMBRE_VALIDO.fullmatch(nombre):
        raise ValueError(
            "Nombre de archivo inválido: usá solo letras, números, espacios, guiones, "
            "puntos y paréntesis (hasta 100 caracteres), sin carpetas."
        )

    # Path(nombre).suffix es la extensión ("cv.PDF" -> ".PDF"); .lower() la normaliza.
    if Path(nombre).suffix.lower() not in EXTENSIONES_PERMITIDAS:
        raise ValueError(f"Extensión no soportada. Se aceptan: {', '.join(EXTENSIONES_PERMITIDAS)}")

    return nombre


def validar_contenido(contenido: bytes) -> None:
    """Lanza ValueError si el archivo es demasiado grande o no es texto UTF-8."""
    if len(contenido) > TAMANO_MAXIMO_BYTES:
        raise ValueError(f"El archivo supera el máximo de {TAMANO_MAXIMO_BYTES // 1000} KB.")

    # ingest.py lee todo en UTF-8. Si un archivo no lo es y se guarda igual, TODOS los
    # reindexados siguientes explotarían. Mejor rechazarlo antes de escribirlo a disco.
    try:
        contenido.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ValueError("El archivo debe estar codificado en UTF-8.") from error
