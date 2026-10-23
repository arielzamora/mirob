"""Tests del reindexado automático: get_state() detecta cuando cambian los archivos de un tenant."""

from pathlib import Path

import pytest

import app.reindex as reindex_module
from app.reindex import SEGUNDOS_ENTRE_REVISIONES, get_state, reindex

TENANT = "uno"


class RelojFalso:
    """Reloj que avanza solo cuando el test lo dice."""

    def __init__(self) -> None:
        self.ahora = 1000.0

    def __call__(self) -> float:
        return self.ahora

    def avanzar(self, segundos: float) -> None:
        self.ahora += segundos


@pytest.fixture
def carpeta_viva(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, RelojFalso]:
    """Un tenant con un archivo, ya indexado, y un reloj controlado por el test."""
    reloj = RelojFalso()
    monkeypatch.setattr(reindex_module, "_reloj", reloj)
    monkeypatch.setattr(reindex_module, "_states", {})
    carpeta = tmp_path / "uno"
    carpeta.mkdir()
    (carpeta / "pan.md").write_text("## Pan\nP: ¿Qué hornea?\nR: Hornea pan.", encoding="utf-8")
    reindex(TENANT, carpeta)
    return carpeta, reloj


def test_un_archivo_nuevo_se_detecta_despues_del_intervalo(carpeta_viva: tuple[Path, RelojFalso]) -> None:
    carpeta, reloj = carpeta_viva
    (carpeta / "tortas.md").write_text("## Tortas\nP: ¿Qué tortas hay?\nR: Hay tortas.", encoding="utf-8")

    reloj.avanzar(SEGUNDOS_ENTRE_REVISIONES + 1)

    assert sorted(get_state(TENANT).corpus["source"].unique()) == ["pan.md", "tortas.md"]


def test_un_archivo_modificado_se_detecta(carpeta_viva: tuple[Path, RelojFalso]) -> None:
    carpeta, reloj = carpeta_viva
    (carpeta / "pan.md").write_text("## Pan\nP: ¿Qué hornea?\nR: Hornea pan y facturas.", encoding="utf-8")

    reloj.avanzar(SEGUNDOS_ENTRE_REVISIONES + 1)

    assert "facturas" in get_state(TENANT).corpus.iloc[0]["text"]


def test_un_archivo_borrado_se_detecta(carpeta_viva: tuple[Path, RelojFalso]) -> None:
    carpeta, reloj = carpeta_viva
    (carpeta / "tortas.md").write_text("## Tortas\nP: ¿Qué tortas hay?\nR: Hay tortas.", encoding="utf-8")
    reloj.avanzar(SEGUNDOS_ENTRE_REVISIONES + 1)
    assert len(get_state(TENANT).corpus) == 2

    (carpeta / "tortas.md").unlink()
    reloj.avanzar(SEGUNDOS_ENTRE_REVISIONES + 1)

    assert get_state(TENANT).corpus["source"].tolist() == ["pan.md"]


def test_dentro_del_intervalo_no_se_revisa_el_disco(carpeta_viva: tuple[Path, RelojFalso]) -> None:
    carpeta, reloj = carpeta_viva
    (carpeta / "tortas.md").write_text("## Tortas\nP: ¿Qué tortas hay?\nR: Hay tortas.", encoding="utf-8")

    reloj.avanzar(SEGUNDOS_ENTRE_REVISIONES - 1)  # todavía no toca revisar

    assert get_state(TENANT).corpus["source"].tolist() == ["pan.md"]


def test_sin_cambios_no_se_reconstruye_el_indice(carpeta_viva: tuple[Path, RelojFalso]) -> None:
    _, reloj = carpeta_viva
    antes = get_state(TENANT)

    reloj.avanzar(SEGUNDOS_ENTRE_REVISIONES + 1)
    despues = get_state(TENANT)

    assert despues is antes  # el mismo objeto: no se rehízo el TF-IDF


def test_despues_de_revisar_sin_cambios_la_proxima_revision_espera_otro_intervalo(
    carpeta_viva: tuple[Path, RelojFalso],
) -> None:
    carpeta, reloj = carpeta_viva
    reloj.avanzar(SEGUNDOS_ENTRE_REVISIONES + 1)
    get_state(TENANT)  # revisa: no hay cambios y anota la hora de la revisión

    (carpeta / "tortas.md").write_text("## Tortas\nP: ¿Qué tortas hay?\nR: Hay tortas.", encoding="utf-8")
    reloj.avanzar(5)  # solo pasaron 5 s desde la última revisión

    assert get_state(TENANT).corpus["source"].tolist() == ["pan.md"]
