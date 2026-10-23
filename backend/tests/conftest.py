"""Fixtures compartidas por todos los tests.

conftest.py es un archivo especial de pytest: lo que se define acá queda
disponible en todos los tests de la carpeta sin importarlo. Una "fixture"
es como un [SetUp] / fixture de xUnit: un objeto ya preparado que pytest
inyecta por NOMBRE de parámetro (parecido a la inyección por constructor).

Regla de oro: los tests usan SIEMPRE el perfil ficticio (data/sample/) y
nunca el contenido real de data/, para que den el mismo resultado en
cualquier máquina y no expongan datos personales.
"""

from pathlib import Path

import pandas as pd
import pytest
from fastapi.testclient import TestClient
from scipy.sparse import csr_matrix
from sklearn.feature_extraction.text import TfidfVectorizer

import app.api.routes as routes_module
import app.reindex as reindex_module
from app.api.security import VAR_ADMIN_API_KEY
from app.main import app
from app.rag.embeddings import fit_vectorizer
from app.rag.ingest import build_corpus
from app.reindex import SAMPLE_DIR


@pytest.fixture(scope="session")
def sample_dir() -> Path:
    """Carpeta del perfil ficticio que usan todos los tests."""
    # scope="session": se crea una sola vez para toda la corrida de tests
    # (como un IClassFixture/ICollectionFixture de xUnit compartido).
    return SAMPLE_DIR


@pytest.fixture(scope="session")
def corpus(sample_dir: Path) -> pd.DataFrame:
    """DataFrame de chunks del perfil ficticio (una fila por chunk)."""
    # build_corpus es determinístico (ordena los archivos), así que se puede
    # compartir entre tests sin riesgo: ningún test lo modifica.
    return build_corpus(sample_dir)


@pytest.fixture(scope="session")
def indice(corpus: pd.DataFrame) -> tuple[TfidfVectorizer, csr_matrix]:
    """(vectorizer, matriz TF-IDF) ya ajustados sobre el corpus ficticio."""
    # Devuelve una tupla; los tests la desempaquetan con `vectorizer, matriz = indice`.
    return fit_vectorizer(corpus)


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch, sample_dir: Path) -> TestClient:
    """Cliente HTTP en memoria con el índice cargado desde el perfil ficticio.

    Es el equivalente a WebApplicationFactory + HttpClient de ASP.NET Core:
    llama a la API sin levantar un servidor ni abrir un puerto.
    """
    # monkeypatch cambia un atributo SOLO durante este test y lo restaura solo
    # al terminar (como reemplazar una dependencia estática y volver atrás).
    # Ponemos `_state` en None para que el estado global original se restaure
    # al final y los tests no se contaminen entre sí.
    monkeypatch.setattr(reindex_module, "_state", None)
    # reindex() con carpeta explícita deja el índice global armado con el
    # perfil ficticio, sin importar si existe un data/ real en esta máquina.
    reindex_module.reindex(sample_dir)
    return TestClient(app)


# Clave de admin que usan los tests. Es un valor de prueba, no un secreto real.
CLAVE_ADMIN_TEST = "clave-de-prueba-123"


@pytest.fixture
def headers_admin() -> dict[str, str]:
    """Headers con la API key correcta, para las llamadas a /admin/content."""
    return {"X-API-Key": CLAVE_ADMIN_TEST}


@pytest.fixture
def client_aislado(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> tuple[TestClient, Path]:
    """Cliente cuyo "data/" es una carpeta temporal vacía (para probar la subida).

    Así POST /admin/content escribe en tmp_path y NUNCA toca el data/ real.
    Además configura la API key de admin con un valor de prueba.
    """
    # setenv deja la variable definida solo durante este test (se restaura sola),
    # y pisa cualquier valor que tengas en tu backend/.env real.
    monkeypatch.setenv(VAR_ADMIN_API_KEY, CLAVE_ADMIN_TEST)
    # tmp_path es una carpeta temporal única por test que pytest borra sola
    # (como Path.GetTempPath() + limpieza automática).
    contenido = tmp_path / "data"
    contenido.mkdir()
    # routes.py importó CONTENT_DIR por nombre, así que hay que parchear ese
    # nombre ahí; reindex.py lo usa dentro de resolver_carpeta_contenido().
    monkeypatch.setattr(routes_module, "CONTENT_DIR", contenido)
    monkeypatch.setattr(reindex_module, "CONTENT_DIR", contenido)
    # Respaldo ficticio vacío: si "data/" está vacío NO queremos que caiga a
    # sample/, así probamos el caso "sin contenido" de verdad.
    monkeypatch.setattr(reindex_module, "SAMPLE_DIR", tmp_path / "sin_sample")
    # Estado global limpio (y restaurado al terminar el test).
    monkeypatch.setattr(reindex_module, "_state", None)
    return TestClient(app), contenido
