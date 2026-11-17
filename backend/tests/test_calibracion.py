"""Tests de la calibración (app/rag/calibracion.py): lógica pura con puntajes inventados, sin red ni Azure."""

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

import app.tenants as tenants_module
from app.rag.calibracion import (
    CalibracionError,
    PreguntaEnTema,
    PreguntasCalibracion,
    calibrar,
    cargar_preguntas,
    indices_esperados,
    reporte,
    sugerir_limites,
    sugerir_umbral,
)
from app.tenants import obtener_tenant

CORPUS = pd.DataFrame(
    {
        "chunk_id": [0, 1, 2],
        "source": ["a.md"] * 3,
        "text": ["## Contacto\nP: ¿Cómo los contacto?\nR: Por email.", "## Idiomas\nP: ¿Qué idiomas?\nR: Español.", "## Precios\nP: ¿Cuánto?\nR: Consultar."],
    }
)


def _escribir(tmp_path: Path, datos: object) -> Path:
    ruta = tmp_path / "calibracion.json"
    ruta.write_text(json.dumps(datos), encoding="utf-8")
    return ruta


# --- Archivo de preguntas ------------------------------------------------------------------------------


def test_carga_las_preguntas_en_tema_y_fuera_de_tema(tmp_path: Path) -> None:
    ruta = _escribir(
        tmp_path,
        {
            "en_tema": [{"pregunta": " ¿Tienen WhatsApp? ", "esperado": "## Contacto"}],
            "fuera_de_tema": ["¿Capital de Mongolia?"],
        },
    )

    preguntas = cargar_preguntas(ruta)

    assert preguntas.en_tema == (PreguntaEnTema("¿Tienen WhatsApp?", "## Contacto"),)
    assert preguntas.fuera_de_tema == ("¿Capital de Mongolia?",)
    assert preguntas.todas == ["¿Tienen WhatsApp?", "¿Capital de Mongolia?"]  # primero en tema, después fuera


@pytest.mark.parametrize(
    "datos",
    [
        [],  # no es un objeto
        {"en_tema": [], "fuera_de_tema": ["x"]},  # sin preguntas en tema
        {"en_tema": [{"pregunta": "a", "esperado": "## A"}], "fuera_de_tema": []},  # sin preguntas fuera de tema
        {"en_tema": [{"pregunta": "a"}], "fuera_de_tema": ["x"]},  # falta 'esperado'
        {"en_tema": [{"pregunta": "", "esperado": "## A"}], "fuera_de_tema": ["x"]},  # pregunta vacía
        {"en_tema": ["texto suelto"], "fuera_de_tema": ["x"]},  # no es un objeto
        {"en_tema": [{"pregunta": "a", "esperado": "## A"}], "fuera_de_tema": [1, 2]},  # no son textos
    ],
)
def test_un_archivo_mal_armado_da_un_error_claro(tmp_path: Path, datos: object) -> None:
    with pytest.raises(CalibracionError):
        cargar_preguntas(_escribir(tmp_path, datos))


def test_un_archivo_que_no_existe_o_no_es_json_da_un_error_claro(tmp_path: Path) -> None:
    with pytest.raises(CalibracionError, match="No se pudo leer"):
        cargar_preguntas(tmp_path / "no-existe.json")

    roto = tmp_path / "roto.json"
    roto.write_text("{no es json", encoding="utf-8")
    with pytest.raises(CalibracionError, match="JSON válido"):
        cargar_preguntas(roto)


def test_indices_esperados_ignora_mayusculas_y_los_numerales() -> None:
    assert indices_esperados(CORPUS, "## Idiomas") == {1}
    assert indices_esperados(CORPUS, "idiomas") == {1}
    assert indices_esperados(CORPUS, "  ## PRECIOS ") == {2}


def test_un_encabezado_inexistente_es_un_error_porque_suele_ser_un_typo() -> None:
    with pytest.raises(CalibracionError, match="Idioms"):
        indices_esperados(CORPUS, "## Idioms")


# --- Umbral ------------------------------------------------------------------------------------------


def test_el_umbral_cae_entre_lo_que_debe_responder_y_lo_que_debe_rechazar() -> None:
    aciertos = np.array([True, True, True])
    maximos = np.array([0.50, 0.60, 0.80, 0.10, 0.20])  # en tema: 0,5 a 0,8 | fuera de tema: 0,1 y 0,2

    umbral = sugerir_umbral(aciertos, maximos)

    assert 0.20 < umbral <= 0.50  # separa los dos grupos


def test_si_los_grupos_se_solapan_elige_el_umbral_que_deja_mas_preguntas_bien() -> None:
    aciertos = np.array([True, True, True, True])
    # Una pregunta fuera de tema (0,45) saca más que una en tema (0,30): no hay umbral perfecto.
    maximos = np.array([0.30, 0.60, 0.70, 0.80, 0.45, 0.10])

    umbral = sugerir_umbral(aciertos, maximos)

    aceptadas_en_tema = int((maximos[:4] >= umbral).sum())
    rechazadas_fuera = int((maximos[4:] < umbral).sum())
    assert aceptadas_en_tema + rechazadas_fuera == 5  # 5 de 6 es lo mejor posible acá (se pierde 1)


def test_una_pregunta_en_tema_con_el_chunk_equivocado_no_cuenta_como_acierto() -> None:
    # La tercera (0,40) trajo OTRO chunk: aceptarla no suma, así que el umbral no se baja por ella.
    aciertos = np.array([True, True, False])
    maximos = np.array([0.60, 0.70, 0.40, 0.30])

    umbral = sugerir_umbral(aciertos, maximos)

    assert umbral > 0.40


def test_el_umbral_queda_siempre_entre_cero_y_uno() -> None:
    assert 0.0 <= sugerir_umbral(np.array([True]), np.array([0.99, 0.98])) <= 1.0
    assert 0.0 <= sugerir_umbral(np.array([True]), np.array([0.0, 0.0])) <= 1.0


# --- Piso y techo del híbrido --------------------------------------------------------------------------


def test_el_piso_es_el_mejor_puntaje_de_una_pregunta_fuera_de_tema_y_el_techo_la_mediana_del_chunk_correcto() -> None:
    # 3 preguntas en tema (chunk esperado 0, 1 y 2) y 2 fuera de tema.
    puntajes = [
        np.array([0.60, 0.1, 0.1]),
        np.array([0.1, 0.70, 0.1]),
        np.array([0.1, 0.1, 0.50]),
        np.array([0.25, 0.1, 0.1]),
        np.array([0.20, 0.1, 0.1]),
    ]

    piso, techo = sugerir_limites(puntajes, [{0}, {1}, {2}], en_tema=3)

    assert (piso, techo) == (0.25, 0.60)  # ruido máximo y mediana de 0,6 / 0,7 / 0,5


def test_si_el_techo_no_supera_al_piso_se_separa_un_minimo() -> None:
    puntajes = [np.array([0.30, 0.1]), np.array([0.30, 0.1])]  # lo bueno vale lo mismo que el ruido

    piso, techo = sugerir_limites(puntajes, [{0}], en_tema=1)

    assert techo >= piso + 0.05


# --- Calibración completa ------------------------------------------------------------------------------


def _caso():
    """Tres chunks. El léxico no entiende los sinónimos (puntaje 0) y el semántico sí: el híbrido tiene que aprovechar eso."""
    preguntas = PreguntasCalibracion(
        en_tema=(
            PreguntaEnTema("contacto exacto", "## Contacto"),  # el léxico la entiende, el semántico más o menos
            PreguntaEnTema("lenguas", "## Idiomas"),  # sinónimo: solo el semántico
            PreguntaEnTema("valores", "## Precios"),  # sinónimo: solo el semántico
        ),
        fuera_de_tema=("capital de mongolia", "xyzzy"),
    )
    lexicos = [
        np.array([0.70, 0.0, 0.0]),
        np.array([0.0, 0.0, 0.0]),
        np.array([0.0, 0.0, 0.0]),
        np.array([0.0, 0.0, 0.0]),
        np.array([0.0, 0.0, 0.0]),
    ]
    semanticos = [
        np.array([0.55, 0.30, 0.20]),
        np.array([0.25, 0.65, 0.30]),
        np.array([0.20, 0.30, 0.60]),
        np.array([0.28, 0.22, 0.20]),  # fuera de tema: el semántico siempre da algo (~0,2-0,3)
        np.array([0.25, 0.20, 0.22]),
    ]
    return preguntas, lexicos, semanticos


def test_calibra_los_tres_modos_y_el_semantico_acierta_lo_que_el_lexico_no() -> None:
    preguntas, lexicos, semanticos = _caso()

    calibracion = calibrar(CORPUS, preguntas, lexicos, semanticos)

    assert calibracion.lexico.aciertos == 1  # solo la primera: lo demás tiene puntaje 0 en todo
    assert calibracion.semantico.aciertos == 3
    assert calibracion.hibrido.aciertos == 3
    assert (calibracion.semantico.falsos_rechazos, calibracion.semantico.falsos_aceptos) == (0, 0)
    assert 0.28 < calibracion.semantico.umbral <= 0.55  # entre el ruido máximo (0,28) y lo correcto más bajo (0,55)


def test_el_hibrido_elige_un_peso_que_aprovecha_a_los_dos() -> None:
    preguntas, lexicos, semanticos = _caso()

    calibracion = calibrar(CORPUS, preguntas, lexicos, semanticos)

    pesos_que_aciertan_todo = [peso for peso, r in calibracion.por_peso if r.aciertos == 3]
    assert calibracion.config_hibrido.peso_semantico in pesos_que_aciertan_todo
    assert calibracion.config_hibrido.peso_semantico > 0  # sin semántico no se entienden los sinónimos
    assert calibracion.config_hibrido.umbral == calibracion.hibrido.umbral
    assert len(calibracion.por_peso) == 11  # de 0,0 a 1,0 de a 0,1


def test_a_igual_resultado_el_peso_elegido_es_el_mas_cercano_al_default() -> None:
    preguntas, lexicos, semanticos = _caso()

    calibracion = calibrar(CORPUS, preguntas, lexicos, semanticos)

    empatados = [peso for peso, r in calibracion.por_peso if (r.aciertos, r.falsos_rechazos + r.falsos_aceptos) == (
        calibracion.hibrido.aciertos, calibracion.hibrido.falsos_rechazos + calibracion.hibrido.falsos_aceptos)]
    assert calibracion.config_hibrido.peso_semantico == min(empatados, key=lambda p: abs(p - 0.7))


def test_lista_las_preguntas_cuyo_top_1_fue_otro_chunk() -> None:
    preguntas, lexicos, semanticos = _caso()

    calibracion = calibrar(CORPUS, preguntas, lexicos, semanticos)

    errores = {e.pregunta: e for e in calibracion.errores["lexico"]}
    assert set(errores) == {"lenguas", "valores"}
    assert errores["lenguas"].esperado == "## Idiomas"
    assert calibracion.errores["semantico"] == ()


def test_el_bloque_para_tenant_json_es_aceptado_por_el_registro_de_tenants(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    preguntas, lexicos, semanticos = _caso()
    bloque = calibrar(CORPUS, preguntas, lexicos, semanticos).bloque_tenant_json()

    carpeta = tmp_path / "t1"
    carpeta.mkdir()
    (carpeta / "tenant.json").write_text(json.dumps({"nombre": "T1", **bloque}), encoding="utf-8")
    (carpeta / "prompt.md").write_text("prompt", encoding="utf-8")
    monkeypatch.setattr(tenants_module, "TENANTS_DIR", tmp_path)
    config = obtener_tenant("t1")

    assert config.retrieval.umbral_semantico == bloque["retrieval"]["semantico"]["umbral"]
    assert config.retrieval.hibrido.peso_semantico == bloque["retrieval"]["hibrido"]["peso_semantico"]
    assert "umbral_similitud" not in bloque  # el umbral léxico ya calibrado no se pisa


def test_hace_falta_un_conjunto_de_puntajes_por_pregunta() -> None:
    preguntas, lexicos, semanticos = _caso()

    with pytest.raises(CalibracionError):
        calibrar(CORPUS, preguntas, lexicos[:-1], semanticos)


def test_el_reporte_resume_los_modos_el_peso_elegido_y_los_errores() -> None:
    preguntas, lexicos, semanticos = _caso()

    texto = reporte(calibrar(CORPUS, preguntas, lexicos, semanticos))

    assert "léxico" in texto and "semántico" in texto and "híbrido" in texto
    assert "peso del semántico" in texto.lower()
    assert "'lenguas'" in texto  # una pregunta que el léxico no resuelve aparece con lo esperado
