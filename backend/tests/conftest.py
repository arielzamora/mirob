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

import app.reindex as reindex_module
from app.api.dependencias import obtener_estado_limites, obtener_generador
from app.finops import EstadoLimites, Limites
from app.api.security import VAR_ADMIN_API_KEY
from app.foundry.tipos import MAX_TOKENS_SALIDA, ChatResult, FoundryError
from app.main import app
from app.rag.embeddings import fit_vectorizer
from app.rag.ingest import build_corpus
from app.reindex import SAMPLE_DIR


# Tenant que lee el perfil ficticio (data/sample/) y tenant cuya carpeta es temporal.
# Ambos existen en backend/tenants/ (configuración versionada).
TENANT_DEMO = "demo"
# Tiene que ser un tenant PÚBLICO: estos tests chatean sin sesión. "ariel-zamora" exige access key.
TENANT_AISLADO = "digital-zur"


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
    llama a la API sin levantar un servidor ni abrir un puerto. Los tests de chat
    usan el tenant TENANT_DEMO.
    """
    # monkeypatch cambia un atributo SOLO durante este test y lo restaura solo
    # al terminar (como reemplazar una dependencia estática y volver atrás).
    # Reemplazamos el diccionario de índices por uno vacío: el original se
    # restaura al final y los tests no se contaminan entre sí.
    monkeypatch.setattr(reindex_module, "_states", {})
    # reindex() con carpeta explícita arma el índice del tenant demo con el
    # perfil ficticio, sin importar si existe contenido real en esta máquina.
    reindex_module.reindex(TENANT_DEMO, sample_dir)
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

    Devuelve (cliente, carpeta del tenant TENANT_AISLADO). Así POST /admin/content
    escribe en tmp_path y NUNCA toca el data/ real. Además configura la API key de
    admin con un valor de prueba.
    """
    # setenv deja la variable definida solo durante este test (se restaura sola),
    # y pisa cualquier valor que tengas en tu backend/.env real.
    monkeypatch.setenv(VAR_ADMIN_API_KEY, CLAVE_ADMIN_TEST)
    # tmp_path es una carpeta temporal única por test que pytest borra sola
    # (como Path.GetTempPath() + limpieza automática).
    data = tmp_path / "data"
    data.mkdir()
    # reindex.py usa CONTENT_DIR al armar la carpeta de cada tenant
    # (data/tenants/<tenant_id>/), así que alcanza con parchear ese nombre.
    monkeypatch.setattr(reindex_module, "CONTENT_DIR", data)
    # Estado global limpio (y restaurado al terminar el test).
    monkeypatch.setattr(reindex_module, "_states", {})
    # La carpeta del tenant se crea vacía: es el caso "tenant sin contenido todavía".
    carpeta = data / "tenants" / TENANT_AISLADO
    carpeta.mkdir(parents=True)
    return TestClient(app), carpeta


class GeneradorFalso:
    """Reemplaza al LLM en los tests: no usa red, no gasta tokens y registra cada llamada.

    Cumple el Protocol GeneradorTexto (app/foundry/tipos.py) sin heredar de nada, como
    un mock de una interfaz en C#. Si recibe `error`, lo lanza en vez de responder.
    """

    def __init__(self, texto: str = "Respuesta del modelo.", error: FoundryError | None = None) -> None:
        self.texto = texto
        self.error = error
        # Cada llamada queda como (instrucciones, mensaje, max_tokens_salida).
        self.llamadas: list[tuple[str, str, int]] = []

    def generar(self, instrucciones: str, mensaje: str, max_tokens_salida: int = MAX_TOKENS_SALIDA) -> ChatResult:
        self.llamadas.append((instrucciones, mensaje, max_tokens_salida))
        if self.error is not None:
            raise self.error
        return ChatResult(
            texto=self.texto,
            tokens_entrada=120,
            tokens_salida=30,
            tokens_total=150,
            latencia_ms=42.0,
            deployment="gpt-4o-mini-test",
        )


@pytest.fixture
def generador_falso() -> GeneradorFalso:
    """Inyecta un GeneradorFalso en la API durante el test (y lo saca al terminar)."""
    falso = GeneradorFalso()
    # dependency_overrides es el "registrar otra implementación en el contenedor de DI" de FastAPI.
    app.dependency_overrides[obtener_generador] = lambda: falso
    yield falso
    app.dependency_overrides.pop(obtener_generador, None)


# Límites tan altos que ningún test los toca (salvo los que pidan otros a propósito).
LIMITES_HOLGADOS = Limites(
    chat_por_minuto=100_000,
    foundry_por_hora=100_000,
    admin_por_minuto=100_000,
    acceso_por_minuto=100_000,
    key_por_minuto=100_000,
    tope_diario_global=10**9,
)


def usar_limites(limites: Limites) -> EstadoLimites:
    """Inyecta un EstadoLimites NUEVO con esos límites en la API y lo devuelve (para inspeccionarlo)."""
    estado = EstadoLimites(limites)
    app.dependency_overrides[obtener_estado_limites] = lambda: estado
    return estado


@pytest.fixture(autouse=True)
def limites_holgados():
    """En TODOS los tests: límites enormes y un estado limpio por test.

    autouse=True aplica la fixture a cada test sin pedirla. Sin esto, el contador de
    pedidos por IP del proceso (que es único) se compartiría entre tests y terminaría
    devolviendo 429 en tests que no tienen nada que ver con los límites.
    """
    estado = usar_limites(LIMITES_HOLGADOS)
    yield estado
    app.dependency_overrides.pop(obtener_estado_limites, None)
