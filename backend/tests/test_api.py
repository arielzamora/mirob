"""Tests de la API (POST /chat y POST /admin/content) con un cliente en memoria.

`client` y `client_aislado` vienen de conftest.py. No se abre ningún puerto:
TestClient llama a la app directamente (como WebApplicationFactory en .NET).
"""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.api.security import VAR_ADMIN_API_KEY
from app.api.uploads import TAMANO_MAXIMO_BYTES

# --- POST /chat -----------------------------------------------------------


def test_chat_raw_devuelve_el_chunk_tal_cual(client: TestClient) -> None:
    respuesta = client.post("/chat", json={"pregunta": "¿Qué idiomas hablás?"})

    assert respuesta.status_code == 200
    cuerpo = respuesta.json()
    assert cuerpo["modo"] == "raw"  # el modo por defecto
    assert cuerpo["source"] == "personal.md"
    assert cuerpo["seccion"] == "## Idiomas"
    assert cuerpo["respuesta"].startswith("## Idiomas")  # el chunk completo, sin retocar
    assert 0.0 < cuerpo["score"] <= 1.0


def test_chat_humanized_devuelve_solo_la_respuesta_con_apertura(client: TestClient) -> None:
    respuesta = client.post("/chat", json={"pregunta": "¿Qué idiomas hablás?", "modo": "humanized"})

    cuerpo = respuesta.json()
    assert respuesta.status_code == 200
    assert cuerpo["modo"] == "humanized"
    assert "Español nativo" in cuerpo["respuesta"]
    assert "P:" not in cuerpo["respuesta"]  # extract_answer descartó la pregunta


def test_chat_txt_informa_que_no_tiene_header(client: TestClient) -> None:
    cuerpo = client.post("/chat", json={"pregunta": "¿Para qué sirve MiRob?"}).json()

    assert cuerpo["source"] == "objetivo.txt"
    assert "sin header" in cuerpo["seccion"]


@pytest.mark.parametrize("pregunta", ["", "   "])
def test_chat_rechaza_preguntas_vacias(client: TestClient, pregunta: str) -> None:
    # 422 = Unprocessable Entity: falló la validación de pydantic (como un ModelState inválido).
    assert client.post("/chat", json={"pregunta": pregunta}).status_code == 422


def test_chat_rechaza_un_modo_desconocido(client: TestClient) -> None:
    respuesta = client.post("/chat", json={"pregunta": "hola", "modo": "foundry"})
    # "foundry" todavía no existe como modo: cuando se sume, este test se actualiza.
    assert respuesta.status_code == 422


def test_chat_sin_contenido_responde_503(client_aislado: tuple[TestClient, Path]) -> None:
    client, _ = client_aislado  # desempaqueta la tupla (cliente, carpeta temporal)

    respuesta = client.post("/chat", json={"pregunta": "¿Qué hace?"})

    assert respuesta.status_code == 503
    assert "No hay contenido" in respuesta.json()["detail"]


def test_chat_no_requiere_api_key(client: TestClient) -> None:
    # Solo el admin está protegido: el chat es público.
    assert client.post("/chat", json={"pregunta": "¿Tenés mascotas?"}).status_code == 200


# --- POST /admin/content: autenticación -------------------------------------


def test_admin_sin_api_key_responde_401(client_aislado: tuple[TestClient, Path]) -> None:
    client, carpeta = client_aislado

    respuesta = client.post("/admin/content", files={"file": ("nuevo.md", b"## A\nx", "text/markdown")})

    assert respuesta.status_code == 401
    assert list(carpeta.iterdir()) == []  # sin clave no se guarda nada


def test_admin_con_api_key_incorrecta_responde_401(client_aislado: tuple[TestClient, Path]) -> None:
    client, carpeta = client_aislado

    respuesta = client.post(
        "/admin/content",
        files={"file": ("nuevo.md", b"## A\nx", "text/markdown")},
        headers={"X-API-Key": "clave-equivocada"},
    )

    assert respuesta.status_code == 401
    assert list(carpeta.iterdir()) == []


def test_admin_sin_clave_configurada_en_el_servidor_queda_deshabilitado(
    client_aislado: tuple[TestClient, Path],
    headers_admin: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, carpeta = client_aislado
    # "Fail closed": si el servidor no tiene MIROB_ADMIN_API_KEY, nadie entra,
    # ni siquiera mandando un header.
    monkeypatch.delenv(VAR_ADMIN_API_KEY)

    respuesta = client.post(
        "/admin/content",
        files={"file": ("nuevo.md", b"## A\nx", "text/markdown")},
        headers=headers_admin,
    )

    assert respuesta.status_code == 503
    assert VAR_ADMIN_API_KEY in respuesta.json()["detail"]
    assert list(carpeta.iterdir()) == []


# --- POST /admin/content: subida --------------------------------------------


def test_admin_rechaza_extensiones_no_soportadas(
    client_aislado: tuple[TestClient, Path], headers_admin: dict[str, str]
) -> None:
    client, carpeta = client_aislado

    respuesta = client.post(
        "/admin/content", files={"file": ("cv.pdf", b"%PDF", "application/pdf")}, headers=headers_admin
    )

    assert respuesta.status_code == 422
    assert list(carpeta.iterdir()) == []  # no se guardó nada


def test_admin_guarda_el_archivo_y_reindexa(
    client_aislado: tuple[TestClient, Path], headers_admin: dict[str, str]
) -> None:
    client, carpeta = client_aislado
    contenido = "## Cafetería\nP: ¿Qué toma?\nR: Toma café con leche.".encode("utf-8")

    respuesta = client.post(
        "/admin/content", files={"file": ("nuevo.md", contenido, "text/markdown")}, headers=headers_admin
    )

    assert respuesta.status_code == 200
    assert respuesta.json() == {"filename": "nuevo.md", "total_chunks": 1, "total_files": 1}
    assert (carpeta / "nuevo.md").exists()

    # Después de subirlo, /chat ya puede responder con ese contenido nuevo.
    chat = client.post("/chat", json={"pregunta": "¿Qué toma?"})
    assert chat.status_code == 200
    assert chat.json()["source"] == "nuevo.md"


@pytest.mark.parametrize(
    "nombre",
    [
        "../evil.md",
        "..\\evil.md",  # en un literal de Python, "\\" es UNA barra invertida (estilo Windows)
        "sub/evil.md",
        "/etc/evil.md",
        ".md",
        "..md",
    ],
)
def test_admin_rechaza_nombres_con_carpetas_o_peligrosos(
    nombre: str, client_aislado: tuple[TestClient, Path], headers_admin: dict[str, str]
) -> None:
    client, carpeta = client_aislado

    respuesta = client.post(
        "/admin/content",
        files={"file": (nombre, b"## X\nP: a\nR: b", "text/markdown")},
        headers=headers_admin,
    )

    assert respuesta.status_code == 422
    # Nada se escribió ni dentro de la carpeta de contenido ni afuera. Antes de este
    # arreglo, "../evil.md" se guardaba en carpeta.parent (el tmp_path del test).
    assert list(carpeta.iterdir()) == []
    assert not (carpeta.parent / "evil.md").exists()


def test_admin_con_ruta_de_windows_guarda_solo_dentro_de_la_carpeta(
    client_aislado: tuple[TestClient, Path], headers_admin: dict[str, str]
) -> None:
    client, carpeta = client_aislado
    contenido = "## Tema\nP: ¿Qué hace?\nR: Hace algo útil.".encode("utf-8")

    respuesta = client.post(
        "/admin/content",
        files={"file": ("C:\\carpeta\\evil.md", contenido, "text/markdown")},
        headers=headers_admin,
    )

    # El parser multipart (python-multipart) ya recorta una ruta estilo Windows a su último
    # tramo antes de que llegue a nuestro código; igual verificamos el resultado: queda
    # guardado como "evil.md" DENTRO de la carpeta de contenido y nada fuera de ella.
    assert respuesta.status_code == 200
    assert [archivo.name for archivo in carpeta.iterdir()] == ["evil.md"]
    assert not (carpeta.parent / "evil.md").exists()


def test_admin_con_contenido_sin_palabras_utiles_no_rompe_el_indice(
    client_aislado: tuple[TestClient, Path], headers_admin: dict[str, str]
) -> None:
    client, _ = client_aislado

    # "a" y "b" son de una sola letra: TF-IDF las ignora y queda vocabulario vacío.
    respuesta = client.post(
        "/admin/content",
        files={"file": ("vacio.md", b"## X\nP: a\nR: b", "text/markdown")},
        headers=headers_admin,
    )

    # Antes esto devolvía 500 y dejaba roto el índice; ahora el corpus queda vacío...
    assert respuesta.status_code == 200
    assert respuesta.json()["total_chunks"] == 0
    # ...y /chat responde con el mismo 503 claro de "no hay contenido".
    assert client.post("/chat", json={"pregunta": "¿Qué hace?"}).status_code == 503


def test_admin_rechaza_archivos_que_no_son_utf8(
    client_aislado: tuple[TestClient, Path], headers_admin: dict[str, str]
) -> None:
    client, carpeta = client_aislado

    # b"\xe9" suelto es "é" en latin-1: UTF-8 inválido, rompería todos los reindexados.
    respuesta = client.post(
        "/admin/content",
        files={"file": ("latin1.md", b"## A\ncaf\xe9", "text/markdown")},
        headers=headers_admin,
    )

    assert respuesta.status_code == 422
    assert "UTF-8" in respuesta.json()["detail"]
    assert list(carpeta.iterdir()) == []


def test_admin_rechaza_archivos_demasiado_grandes(
    client_aislado: tuple[TestClient, Path], headers_admin: dict[str, str]
) -> None:
    client, carpeta = client_aislado
    enorme = b"a" * (TAMANO_MAXIMO_BYTES + 1)

    respuesta = client.post(
        "/admin/content", files={"file": ("grande.txt", enorme, "text/plain")}, headers=headers_admin
    )

    assert respuesta.status_code == 413
    assert list(carpeta.iterdir()) == []
