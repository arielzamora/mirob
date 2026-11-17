"""Almacén de vectores por tenant e indexado incremental (ADR 0009 y 0011).

Calcular embeddings cuesta tokens y tiempo, y el backend escala a cero: sin guardarlos, cada arranque en frío volvería
a vectorizar todo el contenido de cada tenant. Por eso se guardan en la carpeta del propio tenant (en Azure Files):

    <carpeta del tenant>/.vectores/manifest.json        qué hay guardado (deployment, hashes, archivo de vectores)
    <carpeta del tenant>/.vectores/vectores-<id>.npy    la matriz float32, una fila por chunk, ya normalizada

Reglas:
  - Cada tenant guarda SOLO en su carpeta; nunca hay un archivo compartido (ADR 0003). Aunque dos tenants tengan un
    chunk idéntico, cada uno calcula y guarda el suyo: no se leen vectores de otra carpeta.
  - Cada chunk se identifica por el sha256 de su texto. Si el contenido cambia, solo se vectorizan los chunks cuyo hash
    no estaba guardado; el resto se reutiliza. Los hashes que ya no están en el corpus se descartan.
  - Si cambia el deployment (otro modelo), lo guardado no sirve y se recalcula todo.
  - La escritura es atómica: archivo temporal + os.replace (como las access keys). El manifest se reemplaza AL FINAL y
    apunta a un .npy con nombre único: si el proceso se corta, queda el par anterior entero, nunca uno a medias.
  - Guardar es "lo mejor posible": si el share no se puede escribir se avisa en el log y el chat sigue con los vectores
    en memoria. Leer lo guardado también: si algo no cuadra (archivo roto, otro formato) se recalcula, sin error.
  - Los tenants con `usa_sample` (demo) son de solo lectura (ADR 0003): no guardan nada, viven en memoria.
"""

import hashlib
import json
import logging
import os
import re
import tempfile
import threading
import uuid
from collections.abc import Callable
from contextlib import AbstractContextManager, nullcontext
from dataclasses import dataclass
from pathlib import Path
from typing import IO

import numpy as np
import pandas as pd

from app.foundry.tipos import GeneradorEmbeddings
from app.rag.semantico import normalizar

NOMBRE_CARPETA = ".vectores"
ARCHIVO_MANIFEST = "manifest.json"
VERSION_FORMATO = 1

# El manifest es un dato que viene del disco, así que se valida antes de usarlo. En especial el nombre del .npy:
# solo se acepta el que generamos nosotros (sin rutas), para que un manifest alterado no apunte fuera de la carpeta.
_PATRON_ARCHIVO = re.compile(r"vectores-[0-9a-f]{32}\.npy")
_PATRON_HASH = re.compile(r"[0-9a-f]{64}")

logger = logging.getLogger(__name__)

# Un candado por carpeta: serializa leer-calcular-guardar de UN tenant (dos pedidos simultáneos no vectorizan el
# mismo contenido dos veces ni se pisan al escribir) sin frenar a los demás tenants.
_candados: dict[Path, threading.RLock] = {}
_guarda_de_candados = threading.Lock()


def _candado_de(carpeta: Path) -> threading.RLock:
    clave = carpeta.resolve()
    with _guarda_de_candados:
        return _candados.setdefault(clave, threading.RLock())


def hash_chunk(texto: str) -> str:
    """Identifica un chunk por su contenido: el mismo texto da siempre el mismo hash (sha256 en hexadecimal)."""
    return hashlib.sha256(texto.encode("utf-8")).hexdigest()


def _escribir_atomico(ruta: Path, escribir: Callable[[IO], None], binario: bool) -> None:
    """Escribe en un temporal de la MISMA carpeta y lo renombra encima del destino (atómico dentro del mismo volumen)."""
    descriptor, ruta_temporal = tempfile.mkstemp(dir=ruta.parent, suffix=".tmp")
    try:
        with os.fdopen(descriptor, "wb" if binario else "w", **({} if binario else {"encoding": "utf-8"})) as archivo:
            escribir(archivo)
        os.replace(ruta_temporal, ruta)
    except OSError:
        Path(ruta_temporal).unlink(missing_ok=True)
        raise


class AlmacenVectores:
    """Lee y escribe los vectores de UN tenant en su carpeta. Con `carpeta=None` no persiste nada (solo memoria)."""

    def __init__(self, carpeta: Path | None) -> None:
        self._carpeta = carpeta

    def transaccion(self) -> AbstractContextManager:
        """Bloquea esta carpeta mientras dura el `with`. Sin carpeta no hay nada que proteger."""
        return nullcontext() if self._carpeta is None else _candado_de(self._carpeta)

    # --- Lectura --------------------------------------------------------------------------------

    def leer(self, deployment: str) -> dict[str, np.ndarray]:
        """Los vectores guardados, como {hash del chunk: vector}. Vacío si no hay nada utilizable.

        "Utilizable" = formato y deployment actuales, archivos íntegros y con la forma que dice el manifest. Cualquier
        otra cosa devuelve {} y el llamador recalcula: un archivo roto nunca debe romper el chat.
        """
        if self._carpeta is None or not (self._carpeta / ARCHIVO_MANIFEST).is_file():
            return {}
        try:
            manifest = json.loads((self._carpeta / ARCHIVO_MANIFEST).read_text(encoding="utf-8"))
            if manifest["version"] != VERSION_FORMATO or manifest["deployment"] != deployment:
                return {}  # otro formato u otro modelo: sus vectores no se pueden comparar con los de hoy
            hashes, archivo = manifest["hashes"], manifest["archivo"]
            if not _PATRON_ARCHIVO.fullmatch(archivo) or not all(_PATRON_HASH.fullmatch(h) for h in hashes):
                raise ValueError("manifest con datos inválidos")
            # allow_pickle=False: nunca se ejecuta código guardado en un archivo del disco.
            matriz = np.load(self._carpeta / archivo, allow_pickle=False)
            forma_ok = matriz.ndim == 2 and matriz.shape == (len(hashes), manifest["dimensiones"])
            if not forma_ok or matriz.dtype != np.float32 or not np.isfinite(matriz).all():
                raise ValueError("vectores con forma o contenido inválidos")
        except (OSError, ValueError, KeyError, TypeError) as error:
            # Solo el tipo del error: el manifest tiene hashes del contenido, no va al log.
            logger.warning("Vectores guardados inutilizables; se recalculan (%s).", type(error).__name__)
            return {}
        return dict(zip(hashes, matriz, strict=True))

    # --- Escritura --------------------------------------------------------------------------------

    def guardar(self, deployment: str, hashes: list[str], matriz: np.ndarray) -> None:
        """Guarda la matriz (una fila por hash, en ese orden) y descarta lo anterior. Nunca lanza."""
        if self._carpeta is None:
            return
        carpeta = self._carpeta
        with _candado_de(carpeta):
            nuevo_npy = None
            try:
                carpeta.mkdir(parents=True, exist_ok=True)
                nombre = f"vectores-{uuid.uuid4().hex}.npy"
                nuevo_npy = carpeta / nombre
                _escribir_atomico(
                    nuevo_npy, lambda f: np.save(f, matriz.astype(np.float32), allow_pickle=False), binario=True
                )
                manifest = {
                    "version": VERSION_FORMATO,
                    "deployment": deployment,
                    "dimensiones": int(matriz.shape[1]),
                    "archivo": nombre,
                    "hashes": hashes,
                }
                # El manifest se reemplaza AL FINAL: es el punto de confirmación. Antes de eso, lo anterior sigue vigente.
                _escribir_atomico(
                    carpeta / ARCHIVO_MANIFEST,
                    lambda f: json.dump(manifest, f, ensure_ascii=False, indent=2),
                    binario=False,
                )
            except OSError as error:
                if nuevo_npy is not None:
                    nuevo_npy.unlink(missing_ok=True)  # el .npy nuevo quedaría huérfano
                logger.warning("No se pudieron guardar los vectores (siguen en memoria): %s", type(error).__name__)
                return
            self._limpiar(conservar=nombre)

    def invalidar(self) -> None:
        """Borra lo guardado (por ejemplo, si el modelo cambió de dimensiones). El próximo pedido recalcula todo."""
        if self._carpeta is None:
            return
        with _candado_de(self._carpeta):
            (self._carpeta / ARCHIVO_MANIFEST).unlink(missing_ok=True)
            self._limpiar(conservar=None)

    def _limpiar(self, conservar: str | None) -> None:
        """Borra los .npy viejos y los temporales de escrituras cortadas. Mejor esfuerzo: un fallo no importa."""
        if self._carpeta is None or not self._carpeta.is_dir():
            return
        for ruta in [*self._carpeta.glob("vectores-*.npy"), *self._carpeta.glob("*.tmp")]:
            if ruta.name != conservar:
                try:
                    ruta.unlink()
                except OSError:
                    pass


# --- Indexado incremental ----------------------------------------------------------------------


@dataclass(frozen=True)
class Vectorizacion:
    """El resultado de armar los vectores de un corpus: la matriz y cuánto costó."""

    matriz: np.ndarray  # una fila por chunk del corpus, en su orden, normalizada
    tokens: int  # tokens de embeddings gastados ahora (0 si todo estaba guardado)
    nuevos: int  # chunks (textos únicos) que hubo que vectorizar
    reutilizados: int  # chunks (textos únicos) que se tomaron de lo guardado


def construir_vectores(corpus: pd.DataFrame, embeddings: GeneradorEmbeddings, almacen: AlmacenVectores) -> Vectorizacion:
    """Vectores normalizados de todos los chunks, calculando solo los que no estaban guardados.

    Lanza FoundryError (o FoundryNoConfigurado) si falla Azure; en ese caso no se guarda nada.
    """
    textos = list(corpus["text"])
    if not textos:
        return Vectorizacion(np.empty((0, 0), dtype=np.float32), 0, 0, 0)

    hashes = [hash_chunk(texto) for texto in textos]
    # Un texto repetido se vectoriza una sola vez. dict conserva el orden de aparición.
    texto_de = dict(zip(hashes, textos))
    deployment = embeddings.deployment()

    with almacen.transaccion():
        guardados = almacen.leer(deployment)
        pendientes = [h for h in texto_de if h not in guardados]
        tokens = 0

        if pendientes:
            resultado = embeddings.embeber([texto_de[h] for h in pendientes])
            nuevos = normalizar(resultado.vectores)
            tokens = resultado.tokens
            if guardados and next(iter(guardados.values())).shape[0] != nuevos.shape[1]:
                # El modelo cambió de dimensiones bajo el mismo nombre de deployment: lo guardado no sirve.
                guardados, pendientes = {}, list(texto_de)
                resultado = embeddings.embeber([texto_de[h] for h in pendientes])
                nuevos = normalizar(resultado.vectores)
                tokens += resultado.tokens
            guardados.update(zip(pendientes, nuevos))

        unicos = list(texto_de)
        hay_sobrantes = len(guardados) != len(unicos)  # hashes de chunks que ya no existen
        if pendientes or hay_sobrantes:
            almacen.guardar(deployment, unicos, np.stack([guardados[h] for h in unicos]))

        matriz = np.stack([guardados[h] for h in hashes])

    return Vectorizacion(matriz, tokens, nuevos=len(pendientes), reutilizados=len(texto_de) - len(pendientes))
