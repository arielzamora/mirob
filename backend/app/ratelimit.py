"""Rate limiting en memoria: máximo N pedidos por clave (por ejemplo, una IP) en una ventana de tiempo.

Es una "ventana deslizante": se guarda cuándo ocurrió cada pedido reciente y se descartan
los que ya salieron de la ventana. Más preciso que un contador que se reinicia cada minuto
(que deja pasar el doble justo en el borde).

LÍMITE CONOCIDO: el estado vive en la memoria del proceso. Con varias réplicas (o si el
contenedor se reinicia) cada una cuenta por separado. Alcanza para frenar abusos de un
solo origen; un límite global exacto necesitaría un almacén compartido (Redis, etc.).
"""

import threading
import time
from collections import deque
from collections.abc import Callable

# Cada tanto se purgan las claves viejas para que el diccionario no crezca sin límite
# si llegan muchas IPs distintas.
_CLAVES_PARA_PURGAR = 10_000


class LimitadorVentana:
    """Permite como máximo `maximo` pedidos por clave dentro de `ventana_segundos`."""

    def __init__(
        self,
        maximo: int,
        ventana_segundos: float,
        reloj: Callable[[], float] = time.monotonic,
    ) -> None:
        self._maximo = maximo
        self._ventana = ventana_segundos
        # El reloj se inyecta para poder simular el paso del tiempo en los tests, sin dormir.
        self._reloj = reloj
        self._pedidos: dict[str, deque[float]] = {}
        # FastAPI atiende los endpoints síncronos en varios hilos: el lock evita carreras.
        self._lock = threading.Lock()

    def intentar(self, clave: str) -> float | None:
        """Registra un pedido. Devuelve None si pasa, o los segundos que faltan para poder reintentar."""
        ahora = self._reloj()
        with self._lock:
            if len(self._pedidos) > _CLAVES_PARA_PURGAR:
                self._purgar(ahora)

            historial = self._pedidos.setdefault(clave, deque())
            # Descarta los pedidos que ya salieron de la ventana.
            while historial and ahora - historial[0] >= self._ventana:
                historial.popleft()

            if len(historial) >= self._maximo:
                # El más viejo de la ventana es el primero que "caduca": ahí se libera un lugar.
                return max(self._ventana - (ahora - historial[0]), 0.0)

            historial.append(ahora)
            return None

    def _purgar(self, ahora: float) -> None:
        vencidas = [c for c, h in self._pedidos.items() if not h or ahora - h[-1] >= self._ventana]
        for clave in vencidas:
            del self._pedidos[clave]
