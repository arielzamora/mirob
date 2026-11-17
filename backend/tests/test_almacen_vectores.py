"""Tests del almacén de vectores y del indexado incremental (app/rag/almacen_vectores.py). Sin red: EmbeddingsFalso.

Cada test usa carpetas temporales (tmp_path): nunca tocan backend/data/ real.
"""

import json
import os
import shutil
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

import app.rag.almacen_vectores as almacen_module
import app.reindex as reindex_module
from app.rag.almacen_vectores import ARCHIVO_MANIFEST, NOMBRE_CARPETA, AlmacenVectores, construir_vectores, hash_chunk
from app.rag.buscar import MOTIVO_FALLO, buscar
from app.reindex import SAMPLE_DIR
from app.tenants import ConfigRetrieval, obtener_tenant
from tests.conftest import TENANT_DEMO, EmbeddingsFalso

DEPLOYMENT = "embeddings-test"


def _corpus(*textos: str) -> pd.DataFrame:
    return pd.DataFrame({"chunk_id": range(len(textos)), "source": ["a.md"] * len(textos), "text": list(textos)})


def _almacen(carpeta_tenant: Path) -> AlmacenVectores:
    return AlmacenVectores(carpeta_tenant / NOMBRE_CARPETA)


def _manifest(carpeta_tenant: Path) -> dict:
    return json.loads((carpeta_tenant / NOMBRE_CARPETA / ARCHIVO_MANIFEST).read_text(encoding="utf-8"))


def _npys(carpeta_tenant: Path) -> list[Path]:
    return sorted((carpeta_tenant / NOMBRE_CARPETA).glob("vectores-*.npy"))


def test_hash_chunk_es_estable_y_distingue_textos() -> None:
    assert hash_chunk("hola") == hash_chunk("hola")
    assert hash_chunk("hola") != hash_chunk("hola.")
    assert len(hash_chunk("hola")) == 64


# --- Guardar y reutilizar ------------------------------------------------------------------------


def test_la_primera_vez_vectoriza_todo_y_lo_guarda_en_la_carpeta_del_tenant(tmp_path: Path) -> None:
    falso = EmbeddingsFalso()

    resultado = construir_vectores(_corpus("uno dos", "tres cuatro"), falso, _almacen(tmp_path))

    assert falso.llamadas == [["uno dos", "tres cuatro"]]
    assert (resultado.nuevos, resultado.reutilizados) == (2, 0)
    manifest = _manifest(tmp_path)
    assert manifest["deployment"] == DEPLOYMENT
    assert manifest["hashes"] == [hash_chunk("uno dos"), hash_chunk("tres cuatro")]
    assert manifest["dimensiones"] == EmbeddingsFalso.DIMENSIONES
    assert len(_npys(tmp_path)) == 1 and manifest["archivo"] == _npys(tmp_path)[0].name


def test_despues_de_un_reinicio_no_se_vuelve_a_pagar(tmp_path: Path) -> None:
    corpus = _corpus("uno dos", "tres cuatro")
    primera = construir_vectores(corpus, EmbeddingsFalso(), _almacen(tmp_path))

    falso = EmbeddingsFalso()  # un proceso nuevo: sin memoria, lo único que queda es lo guardado en disco
    segunda = construir_vectores(corpus, falso, _almacen(tmp_path))

    assert falso.llamadas == []
    assert segunda.tokens == 0
    assert (segunda.nuevos, segunda.reutilizados) == (0, 2)
    assert np.array_equal(segunda.matriz, primera.matriz)


def test_si_cambia_un_chunk_solo_se_vectoriza_ese(tmp_path: Path) -> None:
    antes = construir_vectores(_corpus("uno dos", "tres cuatro", "cinco seis"), EmbeddingsFalso(), _almacen(tmp_path))

    falso = EmbeddingsFalso()
    despues = construir_vectores(_corpus("uno dos", "tres cuatro CAMBIADO", "cinco seis"), falso, _almacen(tmp_path))

    assert falso.llamadas == [["tres cuatro CAMBIADO"]]  # exactamente el modificado, nada más
    assert despues.tokens == 3
    assert (despues.nuevos, despues.reutilizados) == (1, 2)
    # Los chunks que no cambiaron conservan exactamente su vector.
    assert np.array_equal(despues.matriz[[0, 2]], antes.matriz[[0, 2]])
    assert not np.array_equal(despues.matriz[1], antes.matriz[1])
    assert _manifest(tmp_path)["hashes"] == [hash_chunk(t) for t in ("uno dos", "tres cuatro CAMBIADO", "cinco seis")]
    assert len(_npys(tmp_path)) == 1  # el .npy anterior se borró


def test_al_agregar_y_quitar_chunks_solo_se_calcula_lo_nuevo_y_se_poda_lo_que_sobra(tmp_path: Path) -> None:
    construir_vectores(_corpus("a b", "c d", "e f"), EmbeddingsFalso(), _almacen(tmp_path))

    falso = EmbeddingsFalso()
    construir_vectores(_corpus("a b", "g h"), falso, _almacen(tmp_path))

    assert falso.llamadas == [["g h"]]
    assert _manifest(tmp_path)["hashes"] == [hash_chunk("a b"), hash_chunk("g h")]  # "c d" y "e f" ya no están


def test_si_solo_se_quitan_chunks_no_se_llama_a_azure_pero_se_poda(tmp_path: Path) -> None:
    construir_vectores(_corpus("a b", "c d"), EmbeddingsFalso(), _almacen(tmp_path))

    falso = EmbeddingsFalso()
    construir_vectores(_corpus("a b"), falso, _almacen(tmp_path))

    assert falso.llamadas == []
    assert _manifest(tmp_path)["hashes"] == [hash_chunk("a b")]


def test_un_texto_repetido_se_vectoriza_una_sola_vez(tmp_path: Path) -> None:
    falso = EmbeddingsFalso()

    resultado = construir_vectores(_corpus("x x", "y y", "x x"), falso, _almacen(tmp_path))

    assert falso.llamadas == [["x x", "y y"]]
    assert resultado.matriz.shape[0] == 3  # una fila por chunk del corpus, repetidos incluidos
    assert np.array_equal(resultado.matriz[0], resultado.matriz[2])
    assert len(_manifest(tmp_path)["hashes"]) == 2


def test_un_corpus_vacio_no_llama_a_azure(tmp_path: Path) -> None:
    falso = EmbeddingsFalso()

    resultado = construir_vectores(_corpus(), falso, _almacen(tmp_path))

    assert falso.llamadas == [] and resultado.matriz.shape[0] == 0


# --- Otro modelo --------------------------------------------------------------------------------


def test_si_cambia_el_deployment_se_recalcula_todo(tmp_path: Path) -> None:
    corpus = _corpus("uno dos", "tres cuatro")
    construir_vectores(corpus, EmbeddingsFalso(), _almacen(tmp_path))

    falso = EmbeddingsFalso(nombre_deployment="otro-modelo")
    resultado = construir_vectores(corpus, falso, _almacen(tmp_path))

    assert falso.llamadas == [["uno dos", "tres cuatro"]]  # vectores de otro modelo no se reutilizan
    assert resultado.reutilizados == 0
    assert _manifest(tmp_path)["deployment"] == "otro-modelo"


def test_si_el_modelo_cambia_de_dimensiones_bajo_el_mismo_nombre_se_recalcula_todo(tmp_path: Path) -> None:
    construir_vectores(_corpus("uno dos", "tres cuatro"), EmbeddingsFalso(), _almacen(tmp_path))

    falso = EmbeddingsFalso(dimensiones=64)
    resultado = construir_vectores(_corpus("uno dos", "tres cuatro NUEVO"), falso, _almacen(tmp_path))

    # Primero pide lo pendiente, detecta las dimensiones distintas y pide también lo "reutilizado".
    assert falso.llamadas == [["tres cuatro NUEVO"], ["uno dos", "tres cuatro NUEVO"]]
    assert resultado.matriz.shape == (2, 64)
    assert _manifest(tmp_path)["dimensiones"] == 64


# --- Escritura atómica -----------------------------------------------------------------------------


def test_si_se_corta_la_escritura_del_manifest_queda_lo_anterior_entero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    v1 = construir_vectores(_corpus("uno dos", "tres cuatro"), EmbeddingsFalso(), _almacen(tmp_path))
    reemplazar_original = os.replace

    def reemplazar_con_corte(origen, destino):
        if Path(destino).name == ARCHIVO_MANIFEST:
            raise OSError("se cortó justo al confirmar")
        return reemplazar_original(origen, destino)

    monkeypatch.setattr(almacen_module.os, "replace", reemplazar_con_corte)

    # El chat sigue funcionando: el resultado nuevo se devuelve igual (queda en memoria) y no se lanza nada.
    v2 = construir_vectores(_corpus("uno dos", "cinco seis"), EmbeddingsFalso(), _almacen(tmp_path))
    assert v2.matriz.shape[0] == 2

    monkeypatch.setattr(almacen_module.os, "replace", reemplazar_original)
    guardado = _almacen(tmp_path).leer(DEPLOYMENT)
    assert set(guardado) == {hash_chunk("uno dos"), hash_chunk("tres cuatro")}  # el par anterior, sin tocar
    assert np.array_equal(guardado[hash_chunk("uno dos")], v1.matriz[0])
    assert len(_npys(tmp_path)) == 1  # el .npy nuevo no quedó huérfano
    assert list((tmp_path / NOMBRE_CARPETA).glob("*.tmp")) == []


def test_si_la_carpeta_no_se_puede_escribir_el_chat_sigue_con_los_vectores_en_memoria(tmp_path: Path) -> None:
    un_archivo = tmp_path / "archivo"
    un_archivo.write_text("no es una carpeta")
    almacen = AlmacenVectores(un_archivo / NOMBRE_CARPETA)  # no se puede crear una carpeta dentro de un archivo

    resultado = construir_vectores(_corpus("uno dos"), EmbeddingsFalso(), almacen)

    assert resultado.matriz.shape[0] == 1


# --- Archivos dañados ---------------------------------------------------------------------------------


def _reescribir_manifest(carpeta: Path, cambiar: Callable[[dict], None]) -> None:
    ruta = carpeta / NOMBRE_CARPETA / ARCHIVO_MANIFEST
    datos = json.loads(ruta.read_text(encoding="utf-8"))
    cambiar(datos)
    ruta.write_text(json.dumps(datos), encoding="utf-8")


def _sobrescribir_npy(carpeta: Path, matriz: np.ndarray, permitir_pickle: bool = False) -> None:
    ruta = _npys(carpeta)[0]
    with ruta.open("wb") as archivo:
        np.save(archivo, matriz, allow_pickle=permitir_pickle)


CORRUPCIONES: dict[str, Callable[[Path], None]] = {
    "manifest_no_es_json": lambda c: (c / NOMBRE_CARPETA / ARCHIVO_MANIFEST).write_text("{no es json"),
    "manifest_sin_hashes": lambda c: _reescribir_manifest(c, lambda m: m.pop("hashes")),
    "version_desconocida": lambda c: _reescribir_manifest(c, lambda m: m.update(version=99)),
    "hash_con_formato_invalido": lambda c: _reescribir_manifest(c, lambda m: m["hashes"].__setitem__(0, "zz")),
    "archivo_fuera_de_la_carpeta": lambda c: _reescribir_manifest(c, lambda m: m.update(archivo="../" + m["archivo"])),
    "dimensiones_que_no_coinciden": lambda c: _reescribir_manifest(c, lambda m: m.update(dimensiones=7)),
    "npy_faltante": lambda c: _npys(c)[0].unlink(),
    "npy_truncado": lambda c: _npys(c)[0].write_bytes(_npys(c)[0].read_bytes()[:20]),
    "filas_de_menos": lambda c: _sobrescribir_npy(c, np.zeros((1, EmbeddingsFalso.DIMENSIONES), dtype=np.float32)),
    "tipo_distinto": lambda c: _sobrescribir_npy(c, np.zeros((2, EmbeddingsFalso.DIMENSIONES), dtype=np.float64)),
    "valores_nan": lambda c: _sobrescribir_npy(c, np.full((2, EmbeddingsFalso.DIMENSIONES), np.nan, dtype=np.float32)),
    "con_pickle": lambda c: _sobrescribir_npy(c, np.array([{"a": 1}, {"b": 2}], dtype=object), permitir_pickle=True),
}


@pytest.mark.parametrize("nombre", list(CORRUPCIONES))
def test_un_archivo_danado_se_ignora_y_se_recalcula_sin_error(tmp_path: Path, nombre: str) -> None:
    corpus = _corpus("uno dos", "tres cuatro")
    construir_vectores(corpus, EmbeddingsFalso(), _almacen(tmp_path))
    CORRUPCIONES[nombre](tmp_path)

    assert _almacen(tmp_path).leer(DEPLOYMENT) == {}  # lo dañado no se usa

    falso = EmbeddingsFalso()
    resultado = construir_vectores(corpus, falso, _almacen(tmp_path))

    assert falso.llamadas == [["uno dos", "tres cuatro"]]  # se reconstruye todo
    assert resultado.matriz.shape == (2, EmbeddingsFalso.DIMENSIONES)
    assert set(_almacen(tmp_path).leer(DEPLOYMENT)) == {hash_chunk("uno dos"), hash_chunk("tres cuatro")}  # y queda sano


# --- Aislamiento entre tenants -----------------------------------------------------------------------


def test_cada_tenant_guarda_y_lee_solo_de_su_carpeta(tmp_path: Path) -> None:
    carpeta_a, carpeta_b = tmp_path / "tenant-a", tmp_path / "tenant-b"
    construir_vectores(_corpus("compartido uno", "solo de a"), EmbeddingsFalso(), _almacen(carpeta_a))

    # B tiene un chunk IDÉNTICO a uno de A: igual lo calcula y lo guarda por su cuenta, no lee los vectores de A.
    falso_b = EmbeddingsFalso()
    construir_vectores(_corpus("compartido uno", "solo de b"), falso_b, _almacen(carpeta_b))

    assert falso_b.llamadas == [["compartido uno", "solo de b"]]
    assert set(_manifest(carpeta_a)["hashes"]) == {hash_chunk("compartido uno"), hash_chunk("solo de a")}
    assert set(_manifest(carpeta_b)["hashes"]) == {hash_chunk("compartido uno"), hash_chunk("solo de b")}
    assert hash_chunk("solo de b") not in _almacen(carpeta_a).leer(DEPLOYMENT)
    assert hash_chunk("solo de a") not in _almacen(carpeta_b).leer(DEPLOYMENT)


def test_modificar_un_tenant_no_toca_los_archivos_de_otro(tmp_path: Path) -> None:
    carpeta_a, carpeta_b = tmp_path / "tenant-a", tmp_path / "tenant-b"
    construir_vectores(_corpus("uno a", "dos a"), EmbeddingsFalso(), _almacen(carpeta_a))
    construir_vectores(_corpus("uno b", "dos b"), EmbeddingsFalso(), _almacen(carpeta_b))
    antes_b = {p.name: p.read_bytes() for p in (carpeta_b / NOMBRE_CARPETA).iterdir()}

    construir_vectores(_corpus("uno a", "dos a CAMBIADO", "tres a"), EmbeddingsFalso(), _almacen(carpeta_a))

    assert {p.name: p.read_bytes() for p in (carpeta_b / NOMBRE_CARPETA).iterdir()} == antes_b


def test_si_se_borran_los_vectores_de_un_tenant_se_recalculan_aunque_otro_tenga_el_mismo_chunk(tmp_path: Path) -> None:
    carpeta_a, carpeta_b = tmp_path / "tenant-a", tmp_path / "tenant-b"
    corpus = _corpus("mismo texto", "otro texto")
    construir_vectores(corpus, EmbeddingsFalso(), _almacen(carpeta_a))
    construir_vectores(corpus, EmbeddingsFalso(), _almacen(carpeta_b))
    shutil.rmtree(carpeta_b / NOMBRE_CARPETA)

    falso = EmbeddingsFalso()
    construir_vectores(corpus, falso, _almacen(carpeta_b))

    assert falso.llamadas == [["mismo texto", "otro texto"]]


def test_un_tenant_de_solo_lectura_no_guarda_nada(tmp_path: Path) -> None:
    almacen = AlmacenVectores(None)  # como el demo (`usa_sample`)
    falso = EmbeddingsFalso()

    construir_vectores(_corpus("uno dos"), falso, almacen)
    construir_vectores(_corpus("uno dos"), falso, almacen)

    assert falso.llamadas == [["uno dos"], ["uno dos"]]  # sin persistencia: vuelve a calcular
    assert list(tmp_path.iterdir()) == []


def test_dos_pedidos_simultaneos_vectorizan_el_contenido_una_sola_vez(tmp_path: Path) -> None:
    falso = EmbeddingsFalso()
    corpus = _corpus("uno dos", "tres cuatro", "cinco seis")

    with ThreadPoolExecutor(max_workers=4) as pool:
        futuros = [pool.submit(construir_vectores, corpus, falso, _almacen(tmp_path)) for _ in range(4)]
        resultados = [futuro.result() for futuro in futuros]

    assert len(falso.llamadas) == 1  # el primero calcula y guarda; los demás leen lo guardado
    assert all(np.array_equal(r.matriz, resultados[0].matriz) for r in resultados)


# --- Integración con buscar y reindex ----------------------------------------------------------------------


@pytest.fixture
def contenido(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Una copia del perfil ficticio en una carpeta temporal escribible, como si fuera la de un tenant real."""
    monkeypatch.setattr(reindex_module, "_states", {})
    destino = tmp_path / "tenant-real"
    shutil.copytree(SAMPLE_DIR, destino)
    return destino


@pytest.fixture
def tenant_real():
    """El tenant demo, pero NO de solo lectura y con modo semántico calibrado: se comporta como uno real."""
    return replace(obtener_tenant(TENANT_DEMO), usa_sample=False, retrieval=ConfigRetrieval(umbral_semantico=0.05))


def _preguntar(contenido: Path, tenant, falso, pregunta: str = "lenguas"):
    """Arma el índice desde cero (como tras un reinicio) y hace una consulta semántica."""
    indice = reindex_module.reindex(TENANT_DEMO, contenido)
    return indice, buscar("semantico", pregunta, tenant, indice, falso, k=3)


def test_la_primera_consulta_guarda_los_vectores_y_tras_un_reinicio_solo_se_embebe_la_pregunta(
    contenido: Path, tenant_real
) -> None:
    primero = EmbeddingsFalso(sinonimos={"lenguas": "idiomas"})
    _, busqueda = _preguntar(contenido, tenant_real, primero)
    assert busqueda.retrieval_usado == "semantico"
    assert (contenido / NOMBRE_CARPETA / ARCHIVO_MANIFEST).is_file()

    reinicio = EmbeddingsFalso(sinonimos={"lenguas": "idiomas"})
    _, busqueda = _preguntar(contenido, tenant_real, reinicio)

    assert reinicio.llamadas == [["lenguas"]]  # ni un chunk: todo salió del disco
    assert busqueda.resultado.loc[0, "text"].startswith("## Idiomas")


def test_al_agregar_un_chunk_al_contenido_solo_se_vectoriza_el_nuevo(contenido: Path, tenant_real) -> None:
    _preguntar(contenido, tenant_real, EmbeddingsFalso())
    (contenido / "extra.md").write_text("## Extra\nP: ¿Algo nuevo?\nR: Un dato nuevo.\n", encoding="utf-8")

    falso = EmbeddingsFalso()
    _preguntar(contenido, tenant_real, falso)

    assert len(falso.llamadas) == 2  # [chunk nuevo] y [pregunta]
    assert len(falso.llamadas[0]) == 1 and falso.llamadas[0][0].startswith("## Extra")


def test_guardar_los_vectores_no_cambia_la_huella_del_contenido(contenido: Path, tenant_real) -> None:
    antes = reindex_module._firma_de_carpeta(contenido)

    _preguntar(contenido, tenant_real, EmbeddingsFalso())

    # Si la carpeta .vectores contara como contenido, cada consulta provocaría un reindexado.
    assert reindex_module._firma_de_carpeta(contenido) == antes


def test_el_tenant_demo_de_solo_lectura_no_escribe_en_el_perfil_ficticio(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(reindex_module, "_states", {})
    demo = replace(obtener_tenant(TENANT_DEMO), retrieval=ConfigRetrieval(umbral_semantico=0.05))
    indice = reindex_module.reindex(TENANT_DEMO, SAMPLE_DIR)

    busqueda = buscar("semantico", "idiomas", demo, indice, EmbeddingsFalso(), k=3)

    assert busqueda.retrieval_usado == "semantico"
    assert not (SAMPLE_DIR / NOMBRE_CARPETA).exists()


def test_si_no_se_pueden_guardar_los_vectores_la_busqueda_semantica_funciona_igual(
    contenido: Path, tenant_real, monkeypatch: pytest.MonkeyPatch
) -> None:
    def sin_escritura(*_args, **_kwargs):
        raise OSError("share de solo lectura")

    monkeypatch.setattr(almacen_module, "_escribir_atomico", sin_escritura)

    _, busqueda = _preguntar(contenido, tenant_real, EmbeddingsFalso(sinonimos={"lenguas": "idiomas"}))

    assert busqueda.retrieval_usado == "semantico"
    assert busqueda.resultado.loc[0, "text"].startswith("## Idiomas")


def test_si_las_dimensiones_de_la_pregunta_no_coinciden_se_descartan_los_vectores_y_se_recalculan(
    contenido: Path, tenant_real
) -> None:
    _preguntar(contenido, tenant_real, EmbeddingsFalso())  # guarda vectores de 4096 dimensiones

    # El mismo deployment ahora devuelve otro tamaño (el modelo cambió por debajo).
    indice, busqueda = _preguntar(contenido, tenant_real, EmbeddingsFalso(dimensiones=64))
    assert busqueda.retrieval_usado == "lexico" and busqueda.motivo_degradacion == MOTIVO_FALLO
    assert indice.vectores is None  # se descartaron de memoria...
    assert not (contenido / NOMBRE_CARPETA / ARCHIVO_MANIFEST).exists()  # ...y de disco

    falso = EmbeddingsFalso(dimensiones=64, sinonimos={"lenguas": "idiomas"})
    siguiente = buscar("semantico", "lenguas", tenant_real, indice, falso, k=3)

    assert siguiente.retrieval_usado == "semantico"  # el pedido siguiente ya funciona, sin intervención
    assert len(falso.llamadas[0]) == len(indice.corpus)
