"""Tests del script scripts/calibrar_retrieval.py: se corre `main()` sobre el perfil ficticio, sin Azure ni red."""

import json
from pathlib import Path

import pytest

import app.reindex as reindex_module
from app.rag.almacen_vectores import NOMBRE_CARPETA
from app.reindex import SAMPLE_DIR
from scripts.calibrar_retrieval import main
from tests.conftest import EmbeddingsFalso


@pytest.fixture(autouse=True)
def indices_limpios(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(reindex_module, "_states", {})


def _bloque_json(salida: str) -> dict:
    """El JSON que el script imprime para pegar en tenant.json."""
    return json.loads(salida[salida.index("\n{\n") : salida.rindex("}") + 1])


def test_calibra_el_tenant_demo_y_muestra_el_bloque_para_tenant_json(capsys: pytest.CaptureFixture[str]) -> None:
    falso = EmbeddingsFalso(sinonimos={"lenguas": "idiomas", "reside": "vive", "conduce": "lidera"})

    codigo = main(["--tenant", "demo"], embeddings=falso)

    salida = capsys.readouterr().out
    assert codigo == 0
    assert "Tenant: demo" in salida and "12 en tema, 4 fuera de tema" in salida
    assert "léxico" in salida and "semántico" in salida and "híbrido" in salida
    bloque = _bloque_json(salida)["retrieval"]
    assert 0.0 <= bloque["semantico"]["umbral"] <= 1.0
    assert set(bloque["hibrido"]) == {
        "umbral", "peso_semantico", "piso_lexico", "techo_lexico", "piso_semantico", "techo_semantico"
    }


def test_las_preguntas_se_mandan_en_una_sola_llamada_aparte_de_los_chunks(capsys: pytest.CaptureFixture[str]) -> None:
    falso = EmbeddingsFalso()

    main(["--tenant", "demo"], embeddings=falso)

    # Una llamada con todos los chunks y una con las 16 preguntas juntas: nunca una llamada por pregunta.
    assert [len(llamada) for llamada in falso.llamadas][1:] == [16]


def test_el_tenant_de_solo_lectura_no_guarda_vectores_en_el_perfil_ficticio() -> None:
    main(["--tenant", "demo"], embeddings=EmbeddingsFalso())

    assert not (SAMPLE_DIR / NOMBRE_CARPETA).exists()


def test_con_fake_corre_sin_azure_y_avisa_que_no_sirve_para_calibrar(capsys: pytest.CaptureFixture[str]) -> None:
    codigo = main(["--tenant", "demo", "--fake"])

    salida = capsys.readouterr().out
    assert codigo == 0
    assert "--fake" in salida and "no sirven para calibrar" in salida
    assert not (SAMPLE_DIR / NOMBRE_CARPETA).exists()


def test_puede_usar_un_archivo_de_preguntas_propio(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    propio = tmp_path / "mis-preguntas.json"
    propio.write_text(
        json.dumps(
            {"en_tema": [{"pregunta": "¿Qué idiomas hablás?", "esperado": "## Idiomas"}], "fuera_de_tema": ["xyzzy"]}
        ),
        encoding="utf-8",
    )

    codigo = main(["--tenant", "demo", "--preguntas", str(propio)], embeddings=EmbeddingsFalso())

    assert codigo == 0
    assert "1 en tema, 1 fuera de tema" in capsys.readouterr().out


@pytest.mark.parametrize(
    ("argumentos", "texto_del_error"),
    [
        (["--tenant", "no-existe"], "no existe"),
        (["--tenant", "Mal Formato"], "tenant_id inválido"),
        (["--tenant", "demo", "--preguntas", "/no/existe.json"], "No se pudo leer"),
    ],
)
def test_los_errores_se_informan_con_un_mensaje_y_codigo_1(
    argumentos: list[str], texto_del_error: str, capsys: pytest.CaptureFixture[str]
) -> None:
    codigo = main(argumentos, embeddings=EmbeddingsFalso())

    assert codigo == 1
    assert texto_del_error in capsys.readouterr().err


def test_un_encabezado_esperado_inexistente_se_informa(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    malo = tmp_path / "malo.json"
    malo.write_text(
        json.dumps({"en_tema": [{"pregunta": "¿Hola?", "esperado": "## Inventado"}], "fuera_de_tema": ["xyzzy"]}),
        encoding="utf-8",
    )

    codigo = main(["--tenant", "demo", "--preguntas", str(malo)], embeddings=EmbeddingsFalso())

    assert codigo == 1
    assert "Inventado" in capsys.readouterr().err
