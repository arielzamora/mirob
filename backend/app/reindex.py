"""Reindexado del corpus: reconstruye los chunks y reajusta el TF-IDF.

Consume la capa /rag (ingest.py, embeddings.py) sin modificarla. Como en
esta etapa no hay base de datos (ver CLAUDE.md), el índice vigente
(corpus + vectorizer + matriz) se guarda en una variable a nivel de
módulo — vive en memoria del proceso y se pierde si se reinicia el
servidor, lo cual está bien para esta etapa.
"""

from dataclasses import dataclass
from pathlib import Path

import pandas as pd
from scipy.sparse import csr_matrix
from sklearn.feature_extraction.text import TfidfVectorizer

from app.rag.embeddings import fit_vectorizer
from app.rag.ingest import build_corpus

# backend/app/reindex.py -> .parent (app/) -> .parent (backend/) -> data/
# Es el contenido REAL de Ariel: la carpeta está en el .gitignore, así que
# nunca se versiona (en .NET sería como un appsettings.Production.json
# que queda fuera del repo).
CONTENT_DIR = Path(__file__).parent.parent / "data"

# Perfil ficticio que SÍ se versiona. Sirve para que un clon del repo
# funcione sin contenido real y para que los tests usen datos estables.
SAMPLE_DIR = CONTENT_DIR / "sample"

# Extensiones que ingest.py sabe leer (mismas que en build_corpus).
_EXTENSIONES_CONTENIDO = (".md", ".txt")


@dataclass
class IndexState:
    """El índice vigente: el corpus de chunks y su vectorización TF-IDF."""

    corpus: pd.DataFrame
    vectorizer: TfidfVectorizer
    matrix: csr_matrix


# None hasta el primer reindex() (o el primer get_state(), que reindexa
# automáticamente si todavía no hay nada cargado).
_state: IndexState | None = None


def _tiene_contenido(carpeta: Path) -> bool:
    """True si `carpeta` existe y tiene al menos un .md/.txt directamente adentro."""
    # is_dir() evita explotar si la carpeta no existe (en un clon nuevo, data/ no está).
    # any(...) corta apenas encuentra uno, como Enumerable.Any(...) en C#.
    # glob("*") no es recursivo: la subcarpeta sample/ no cuenta como contenido real.
    return carpeta.is_dir() and any(
        p.suffix.lower() in _EXTENSIONES_CONTENIDO for p in carpeta.glob("*")
    )


def resolver_carpeta_contenido() -> Path:
    """Elige la carpeta a indexar: el contenido real si existe, si no el perfil de ejemplo."""
    # Orden de preferencia: data/ (real) primero, data/sample/ (ficticio) como respaldo.
    return CONTENT_DIR if _tiene_contenido(CONTENT_DIR) else SAMPLE_DIR


def reindex(content_dir: Path | str | None = None) -> IndexState:
    """Reconstruye el corpus y el índice TF-IDF a partir de `content_dir`.

    Se llama al arrancar la API y cada vez que se sube contenido nuevo
    vía POST /admin/content. Si no se pasa carpeta (None), usa
    resolver_carpeta_contenido(); si se pasa una explícita (por ejemplo
    en un test), se respeta tal cual y NO hay respaldo silencioso.
    """
    global _state
    # None significa "usá el valor por defecto". No se puede poner la llamada
    # a la función directo como valor por defecto del parámetro porque Python
    # lo evaluaría una sola vez al importar el módulo (a diferencia de C#,
    # donde un default tiene que ser constante en tiempo de compilación).
    carpeta = resolver_carpeta_contenido() if content_dir is None else content_dir
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
    _state = IndexState(corpus=corpus, vectorizer=vectorizer, matrix=matrix)
    return _state


def get_state() -> IndexState:
    """Devuelve el índice vigente, reindexando en el momento si todavía no existe."""
    if _state is None:
        return reindex()
    return _state
