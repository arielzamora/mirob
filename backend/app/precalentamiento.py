"""Precalentamiento al arrancar: deja listo lo que, si no, pagaría la primera pregunta de una persona.

Con el backend escalando a cero, cada arranque en frío empieza con todo vacío: sin índice TF-IDF y sin
cliente de Foundry. Los dos se arman "perezosamente" (en el primer uso), así que la primera consulta
tardaba mucho más que las siguientes. Acá se adelantan, en un hilo aparte para que /health y el resto
de la API no esperen. No gasta tokens del modelo.

Recibe las piezas por parámetro (`armar_indice`, `generador`) en vez de importarlas: así se prueba sin
Azure ni contenido real, igual que un servicio de C# que recibe sus dependencias por constructor.
"""

import logging
import threading
from collections.abc import Callable, Iterable
from typing import Protocol, runtime_checkable

logger = logging.getLogger(__name__)


@runtime_checkable
class Precalentable(Protocol):
    """Un generador que sabe prepararse por adelantado (FoundryGenerador). Los falsos de los tests no lo tienen."""

    def precalentar(self) -> None: ...


def precalentar(tenant_ids: Iterable[str], armar_indice: Callable[[str], object], generador: object) -> None:
    """Arma el índice de cada tenant y prepara al generador. Nunca lanza: es una mejora, no un requisito."""
    for tenant_id in tenant_ids:
        try:
            armar_indice(tenant_id)
        except Exception as error:  # noqa: BLE001 - un tenant roto no debe impedir precalentar los demás
            logger.warning("No se pudo precalentar el índice de '%s': %s", tenant_id, type(error).__name__)

    # isinstance con un Protocol runtime_checkable mira si el objeto tiene el método (tipado estructural).
    if isinstance(generador, Precalentable):
        try:
            generador.precalentar()
        except Exception as error:  # noqa: BLE001
            logger.warning("No se pudo precalentar el generador: %s", type(error).__name__)


def precalentar_en_segundo_plano(
    tenant_ids: Iterable[str], armar_indice: Callable[[str], object], generador: object
) -> threading.Thread:
    """Lanza `precalentar` en un hilo daemon (no impide que el proceso termine) y lo devuelve."""
    hilo = threading.Thread(
        target=precalentar,
        args=(list(tenant_ids), armar_indice, generador),
        name="precalentamiento",
        daemon=True,
    )
    hilo.start()
    return hilo
