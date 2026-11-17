"""Fixtures compartidas por todos los tests.

conftest.py es un archivo especial de pytest: lo que se define acá queda
disponible en todos los tests de la carpeta sin importarlo. Una "fixture"
es como un [SetUp] / fixture de xUnit: un objeto ya preparado que pytest
inyecta por NOMBRE de parámetro (parecido a la inyección por constructor).

Regla de oro: los tests usan SIEMPRE el perfil ficticio (data/sample/) y
nunca el contenido real de data/, para que den el mismo resultado en
cualquier máquina y no expongan datos personales.
"""

import re
import zlib
from collections.abc import Sequence
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient
from scipy.sparse import csr_matrix
from sklearn.feature_extraction.text import TfidfVectorizer

import app.reindex as reindex_module
from app.api.dependencias import obtener_embeddings, obtener_estado_limites, obtener_generador
from app.finops import EstadoLimites, Limites
from app.api.security import VAR_ADMIN_API_KEY
from app.foundry.tipos import MAX_TOKENS_SALIDA, ChatResult, FoundryError, FoundryNoConfigurado, ResultadoEmbeddings
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


class EmbeddingsFalso:
    """Reemplaza al modelo de embeddings en los tests: sin red, determinístico y con "sinónimos" a pedido.

    Arma un vector por bolsa de palabras (cada palabra suma 1 en una columna sacada de su hash), así dos textos con
    palabras en común quedan cerca. `sinonimos` traduce una palabra a otra ANTES de vectorizar: es la forma de simular
    que el modelo real entiende que "lenguas" e "idiomas" significan lo mismo, sin Azure.
    Cumple el Protocol GeneradorEmbeddings sin heredar de nada (como un mock de interfaz en C#).
    """

    # Muchas columnas para que dos palabras distintas casi nunca caigan en la misma (una colisión de hash daba un
    # puntaje de 0,1 a una pregunta fuera de tema): con 4096, lo fuera de tema da 0 y los tests son estables.
    DIMENSIONES = 4096

    def __init__(
        self,
        sinonimos: dict[str, str] | None = None,
        error: Exception | None = None,
        nombre_deployment: str = "embeddings-test",
        dimensiones: int | None = None,
    ) -> None:
        self.sinonimos = sinonimos or {}
        self.error = error
        self.nombre_deployment = nombre_deployment  # se puede cambiar en un test para simular otro modelo
        self.dimensiones = dimensiones or self.DIMENSIONES
        # Cada elemento es UNA llamada (un lote): permite afirmar cuántas llamadas se hicieron y con qué textos.
        self.llamadas: list[list[str]] = []

    def deployment(self) -> str:
        return self.nombre_deployment

    def embeber(self, textos: Sequence[str]) -> ResultadoEmbeddings:
        self.llamadas.append(list(textos))
        if self.error is not None:
            raise self.error
        vectores = np.zeros((len(textos), self.dimensiones), dtype=np.float32)
        for fila, texto in enumerate(textos):
            for palabra in re.findall(r"\w+", texto.lower()):
                palabra = self.sinonimos.get(palabra, palabra)
                vectores[fila, zlib.crc32(palabra.encode("utf-8")) % self.dimensiones] += 1.0
        # Un "token" por palabra: no es el conteo real de un modelo, pero es estable y sirve para afirmar el gasto.
        tokens = sum(len(texto.split()) for texto in textos)
        return ResultadoEmbeddings(vectores, tokens, self.nombre_deployment, 1.0)


class EmbeddingsSinConfigurar:
    """Lo que ven los tests por defecto: un generador de embeddings que NUNCA llama a Azure y siempre falla "sin configurar".

    Hay tenants versionados con un modo de embeddings por defecto (digital-zur usa el híbrido). Sin esta protección, cualquier test
    que converse con ellos intentaría llamar al Foundry REAL (si hay un backend/.env con credenciales en la máquina), con costo y
    sin determinismo. Así el chat degrada al léxico, que es justo lo que esos tests esperan.
    """

    def deployment(self) -> str:
        raise FoundryNoConfigurado("Tests: los embeddings reales están desactivados.")

    def embeber(self, textos: Sequence[str]) -> ResultadoEmbeddings:
        raise FoundryNoConfigurado("Tests: los embeddings reales están desactivados.")


@pytest.fixture(autouse=True)
def sin_embeddings_reales():
    """En TODOS los tests: la API usa EmbeddingsSinConfigurar salvo que el test pida otro (fixture `embeddings_falso`)."""
    sin_configurar = EmbeddingsSinConfigurar()
    app.dependency_overrides[obtener_embeddings] = lambda: sin_configurar
    yield
    app.dependency_overrides.pop(obtener_embeddings, None)


@pytest.fixture
def embeddings_falso() -> EmbeddingsFalso:
    """Inyecta un EmbeddingsFalso en la API durante el test (y lo saca al terminar)."""
    falso = EmbeddingsFalso()
    app.dependency_overrides[obtener_embeddings] = lambda: falso
    yield falso
    app.dependency_overrides.pop(obtener_embeddings, None)


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
