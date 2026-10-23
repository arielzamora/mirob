"""Tests de uploads.py: validación del nombre y del contenido de archivos subidos.

Son funciones puras: se prueban sin FastAPI ni disco.
"""

import pytest

from app.api.uploads import TAMANO_MAXIMO_BYTES, validar_contenido, validar_nombre_archivo


@pytest.mark.parametrize(
    "nombre",
    ["tecnica.md", "habilidades-blandas.md", "objetivo.TXT", "Mi perfil (v2).md", "años_de_experiencia.txt"],
)
def test_acepta_nombres_normales(nombre: str) -> None:
    # Devuelve el mismo nombre, sin modificarlo.
    assert validar_nombre_archivo(nombre) == nombre


@pytest.mark.parametrize(
    "nombre",
    [
        None,
        "",
        " ",
        ".md",
        "..md",
        "../x.md",
        "..\\x.md",  # barra invertida de Windows (escapada: "\\" es UNA barra)
        "a/b.md",
        "a\\b.md",
        "/abs.md",
        "C:\\x.md",
        "x\n.md",  # salto de línea dentro del nombre
        "a" * 100 + ".md",  # 103 caracteres: se pasa del máximo de 100
    ],
)
def test_rechaza_nombres_peligrosos_o_invalidos(nombre: str | None) -> None:
    with pytest.raises(ValueError, match="Nombre de archivo inválido"):
        validar_nombre_archivo(nombre)


@pytest.mark.parametrize("nombre", ["cv.pdf", "script.py", "sin_extension", "datos.md.exe"])
def test_rechaza_extensiones_no_soportadas(nombre: str) -> None:
    with pytest.raises(ValueError, match="Extensión no soportada"):
        validar_nombre_archivo(nombre)


def test_contenido_valido_no_lanza() -> None:
    validar_contenido("## Café\nP: ¿Qué?\nR: Nada.".encode("utf-8"))


def test_contenido_en_el_limite_exacto_es_valido() -> None:
    validar_contenido(b"a" * TAMANO_MAXIMO_BYTES)


def test_contenido_que_se_pasa_un_byte_se_rechaza() -> None:
    with pytest.raises(ValueError, match="máximo"):
        validar_contenido(b"a" * (TAMANO_MAXIMO_BYTES + 1))


def test_contenido_que_no_es_utf8_se_rechaza() -> None:
    with pytest.raises(ValueError, match="UTF-8"):
        validar_contenido(b"caf\xe9")
