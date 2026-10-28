"""FoundryGenerador: la implementación real de GeneradorTexto (la única que usa el SDK).

La configuración y los clientes se crean UNA vez, la primera vez que se necesitan, y
se reutilizan en todos los requests (como un HttpClient singleton). Así el arranque
de la app y los tests no dependen de tener Azure configurado, y los pedidos que no
llegan al LLM (modo raw, o por debajo del umbral) funcionan sin Foundry.
"""

import logging
import threading

from azure.core.exceptions import AzureError
from azure.identity import DefaultAzureCredential
from openai import OpenAI, OpenAIError

from app.foundry.chat import generar
from app.foundry.client import crear_cliente_proyecto, crear_credencial, obtener_cliente_openai, pedir_token
from app.foundry.config import FoundryConfigError, FoundrySettings
from app.foundry.tipos import MAX_TOKENS_SALIDA, ChatResult, FoundryError, FoundryNoConfigurado

logger = logging.getLogger(__name__)


class FoundryGenerador:
    """Genera texto con el deployment de chat del proyecto de Foundry."""

    def __init__(self, settings: FoundrySettings | None = None) -> None:
        # None: se lee de las variables de entorno en el primer uso (ver FoundrySettings.from_env).
        self._settings = settings
        self._cliente: OpenAI | None = None
        # Se guarda para poder pedirle el token por adelantado (ver precalentar).
        self._credencial: DefaultAzureCredential | None = None
        # FastAPI atiende los endpoints síncronos en varios hilos a la vez: el lock evita
        # que dos requests simultáneos creen dos clientes (como un lock en C#).
        self._lock = threading.Lock()

    def _obtener_cliente(self) -> tuple[OpenAI, FoundrySettings]:
        with self._lock:
            if self._settings is None:
                try:
                    self._settings = FoundrySettings.from_env()
                except FoundryConfigError as error:
                    raise FoundryNoConfigurado(str(error)) from error
            if self._cliente is None:
                credencial = crear_credencial()
                proyecto = crear_cliente_proyecto(self._settings, credencial)
                self._cliente = obtener_cliente_openai(proyecto)
                self._credencial = credencial
            return self._cliente, self._settings

    def precalentar(self) -> None:
        """Deja listos el cliente y el token de Entra ID antes de la primera pregunta (no llama al modelo).

        Sin esto, la primera respuesta paga la creación de los clientes y la búsqueda del token. Nunca
        lanza: si Foundry no está configurado o falla la autenticación, se avisa en el log y el primer
        pedido real lo intentará de nuevo (y dará el error de siempre).
        """
        try:
            self._obtener_cliente()
            if self._credencial is not None:
                pedir_token(self._credencial)
        except (FoundryNoConfigurado, AzureError, OpenAIError) as error:
            # Solo el TIPO del error: el mensaje podría traer datos del entorno.
            logger.warning("No se pudo precalentar Foundry: %s", type(error).__name__)

    def generar(self, instrucciones: str, mensaje: str, max_tokens_salida: int = MAX_TOKENS_SALIDA) -> ChatResult:
        try:
            cliente, settings = self._obtener_cliente()
            return generar(cliente, settings, instrucciones, mensaje, max_tokens_salida)
        except FoundryError:
            raise
        except (AzureError, OpenAIError) as error:
            # Se loguea el TIPO de error, no el mensaje del usuario ni el prompt (podrían tener datos personales).
            logger.error("Falló la llamada a Foundry: %s", type(error).__name__)
            raise FoundryError("No se pudo obtener respuesta del modelo.") from error
