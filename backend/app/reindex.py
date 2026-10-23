"""Reindexado del corpus por tenant: reconstruye los chunks y reajusta el TF-IDF.

Consume la capa /rag (ingest.py, embeddings.py) sin modificarla. Como en
esta etapa no hay base de datos (ver CLAUDE.md), el índice vigente de CADA
tenant (corpus + vectorizer + matriz) vive en un diccionario a nivel de
módulo — en memoria del proceso, se pierde si se reinicia el servidor, lo
cual está bien para esta etapa.

Aislamiento: cada tenant tiene su propio IndexState, construido SOLO con los
archivos de su carpeta. Nunca se arma una matriz compartida entre tenants, así
que el retrieval de uno no puede ver chunks de otro.
"""

import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import pandas as pd
from scipy.sparse import csr_matrix
from sklearn.feature_extraction.text import TfidfVectorizer

from app.rag.embeddings import fit_vectorizer
from app.rag.ingest import build_corpus
from app.tenants import TenantConfig, obtener_tenant

# backend/app/reindex.py -> .parent (app/) -> .parent (backend/) -> data/
# Es el contenido REAL: la carpeta está en el .gitignore, así que nunca se
# versiona (en .NET sería como un appsettings.Production.json fuera del repo).
CONTENT_DIR = Path(__file__).parent.parent / "data"

# Perfil ficticio que SÍ se versiona. Lo usa el tenant "demo" y los tests, para
# que un clon del repo funcione sin contenido real y los resultados sean estables.
SAMPLE_DIR = CONTENT_DIR / "sample"


# Cada cuántos segundos get_state() revisa si cambiaron los archivos de un tenant. Revisar es leer el
# nombre, tamaño y fecha de unos pocos archivos, pero en Azure Files cada consulta al share tiene su
# costo, así que no se hace en cada pedido.
SEGUNDOS_ENTRE_REVISIONES = 30.0

# Reloj usado para esa espera. Es una variable para poder simular el paso del tiempo en los tests.
_reloj: Callable[[], float] = time.monotonic

# Extensiones que ingest.py sabe leer (las mismas que build_corpus).
_EXTENSIONES_CONTENIDO = (".md", ".txt")


@dataclass
class IndexState:
    """El índice vigente de un tenant: el corpus de chunks y su vectorización TF-IDF."""

    corpus: pd.DataFrame
    vectorizer: TfidfVectorizer
    matrix: csr_matrix
    # Para detectar que el contenido cambió sin reiniciar el backend:
    carpeta: Path  # de dónde se armó el índice
    firma: tuple  # (nombre, fecha de modificación, tamaño) de cada archivo con el que se armó
    revisado_en: float  # cuándo se comparó la firma por última vez (según _reloj)


# tenant_id -> índice. Se completa en el primer reindex() de cada tenant (o en el
# primer get_state(), que reindexa solo si ese tenant todavía no tiene nada).
_states: dict[str, IndexState] = {}


def carpeta_de_contenido(tenant: TenantConfig) -> Path:
    """Carpeta con los .md/.txt del tenant: data/tenants/<tenant_id>/.

    Sin respaldo silencioso: si el tenant no tiene contenido, no se le presta el de
    otro (principio de MiRob: nunca inventar). Solo un tenant que declara
    `usa_sample` en su tenant.json (el "demo") lee el perfil ficticio.
    """
    if tenant.usa_sample:
        return SAMPLE_DIR
    # tenant_id ya pasó validar_tenant_id (solo [a-z0-9-]), así que esta ruta no puede escapar de data/.
    return CONTENT_DIR / "tenants" / tenant.tenant_id


def _firma_de_carpeta(carpeta: Path) -> tuple:
    """Huella de los archivos de contenido: si se agrega, borra o modifica uno, la huella cambia."""
    if not carpeta.is_dir():
        return ()
    archivos = [p for p in carpeta.glob("*") if p.suffix.lower() in _EXTENSIONES_CONTENIDO]
    # stat() devuelve fecha de modificación y tamaño sin leer el archivo.
    return tuple(sorted((p.name, p.stat().st_mtime_ns, p.stat().st_size) for p in archivos))


def reindex(tenant_id: str, content_dir: Path | str | None = None) -> IndexState:
    """Reconstruye el corpus y el índice TF-IDF del tenant a partir de `content_dir`.

    Se llama cada vez que se sube contenido nuevo vía POST /admin/content. Si no
    se pasa carpeta (None), usa carpeta_de_contenido(); si se pasa una explícita
    (por ejemplo en un test), se respeta tal cual.
    """
    # None significa "usá el valor por defecto". No se puede poner la llamada
    # a la función directo como valor por defecto del parámetro porque Python
    # lo evaluaría una sola vez al importar el módulo (a diferencia de C#,
    # donde un default tiene que ser constante en tiempo de compilación).
    carpeta = carpeta_de_contenido(obtener_tenant(tenant_id)) if content_dir is None else content_dir
    # Si la carpeta todavía no existe (tenant nuevo sin contenido), glob("*") no
    # devuelve nada: el tenant queda con corpus vacío y /chat responde 503.
    corpus = build_corpus(carpeta)
    if corpus.empty:
        # TfidfVectorizer().fit_transform() sobre una lista vacía tira
        # ValueError ("empty vocabulary"). Guardamos un estado "vacío"
        # válido en vez de dejar explotar el reindex; la API lo detecta
        # en /chat y devuelve un 503 claro en vez de un 500.
        vectorizer, matrix = TfidfVectorizer(), csr_matrix((0, 0))
    else:
        try:
            vectorizer, matrix = fit_vectorizer(corpus)
        except ValueError:
            # Hay chunks pero ninguno tiene palabras que TF-IDF pueda usar (por
            # ejemplo, solo símbolos o palabras de una letra): sklearn lanza el mismo
            # ValueError "empty vocabulary". Sin este try, un archivo así dejaba
            # TODOS los reindexados rotos (500 en cada request). Lo tratamos como
            # corpus vacío: /chat responde 503 con un mensaje claro.
            # iloc[0:0] deja un DataFrame sin filas pero con las mismas columnas.
            corpus = corpus.iloc[0:0]
            vectorizer, matrix = TfidfVectorizer(), csr_matrix((0, 0))
    carpeta = Path(carpeta)
    _states[tenant_id] = IndexState(
        corpus=corpus,
        vectorizer=vectorizer,
        matrix=matrix,
        carpeta=carpeta,
        firma=_firma_de_carpeta(carpeta),
        revisado_en=_reloj(),
    )
    return _states[tenant_id]


def get_state(tenant_id: str) -> IndexState:
    """Devuelve el índice del tenant, y lo reconstruye solo si hace falta.

    Se reindexa en estos casos:
      - todavía no hay índice, o el cacheado está VACÍO (el tenant no tenía contenido);
      - pasaron SEGUNDOS_ENTRE_REVISIONES y los archivos de su carpeta cambiaron.
    Así, subir o editar archivos en el share de Azure Files se refleja en menos de un minuto, sin
    reiniciar el backend ni llamar a /admin/reindex (que sigue existiendo para forzarlo al instante).
    """
    estado = _states.get(tenant_id)
    if estado is None or estado.corpus.empty:
        return reindex(tenant_id)

    ahora = _reloj()
    if ahora - estado.revisado_en >= SEGUNDOS_ENTRE_REVISIONES:
        if _firma_de_carpeta(estado.carpeta) != estado.firma:
            return reindex(tenant_id, estado.carpeta)
        estado.revisado_en = ahora
    return estado
