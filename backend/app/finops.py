"""Controles de costo y abuso del chat público: límites por IP y tope diario de tokens.

Tres capas, de más barata a más cara (ver docs/adr/0004-gobierno-de-costos-de-ia.md):
  1. Umbral de similitud: bajo el umbral no se llama al LLM (app/rag/generate.py).
  2. Rate limit por IP: frena a quien dispara pedidos en ráfaga.
  3. Tope diario de tokens: corta el gasto total aunque cada IP respete su límite.

Todo vive en memoria del proceso (ver el límite conocido en app/ratelimit.py).
"""

import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

from app.foundry.tipos import MAX_TOKENS_SALIDA, ChatResult, GeneradorTexto
from app.ratelimit import LimitadorVentana
from app.tenants import TenantConfig

VAR_CHAT_POR_MINUTO = "MIROB_RATE_CHAT_PER_MINUTE"
VAR_FOUNDRY_POR_HORA = "MIROB_RATE_FOUNDRY_PER_HOUR"
VAR_ADMIN_POR_MINUTO = "MIROB_RATE_ADMIN_PER_MINUTE"
VAR_ACCESO_POR_MINUTO = "MIROB_RATE_ACCESS_PER_MINUTE"
VAR_KEY_POR_MINUTO = "MIROB_RATE_KEY_PER_MINUTE"
VAR_TOPE_DIARIO = "MIROB_DAILY_TOKEN_CAP"


class LimitesConfigError(ValueError):
    """Una variable de entorno de límites no es un entero positivo."""


class LimiteExcedido(Exception):
    """Una IP superó su límite de pedidos. `reintentar_en` son los segundos de espera."""

    def __init__(self, reintentar_en: float) -> None:
        super().__init__("Demasiados pedidos.")
        self.reintentar_en = reintentar_en


class PresupuestoAgotado(Exception):
    """Se alcanzó el tope diario de tokens (global o del tenant). `reintentar_en`: segundos hasta el próximo día UTC."""

    def __init__(self, reintentar_en: float) -> None:
        super().__init__("Tope diario de tokens alcanzado.")
        self.reintentar_en = reintentar_en


@dataclass(frozen=True)
class Limites:
    """Valores de los límites. Los defaults son conservadores para un sitio chico."""

    chat_por_minuto: int = 20  # pedidos de chat por IP por minuto (cualquier modo)
    foundry_por_hora: int = 30  # llamadas al LLM por IP por hora
    admin_por_minuto: int = 10  # pedidos al admin por IP por minuto (frena la fuerza bruta de la API key)
    acceso_por_minuto: int = 10  # canjes de access key por IP por minuto (frena el que prueba keys al azar)
    key_por_minuto: int = 10  # preguntas por minuto de UNA access key (además del límite por IP)
    tope_diario_global: int = 200_000  # tokens por día (UTC) entre todos los tenants

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> "Limites":
        """Lee los límites de variables de entorno; las que faltan usan el default."""
        defaults = cls()
        return cls(
            chat_por_minuto=_entero(env, VAR_CHAT_POR_MINUTO, defaults.chat_por_minuto),
            foundry_por_hora=_entero(env, VAR_FOUNDRY_POR_HORA, defaults.foundry_por_hora),
            admin_por_minuto=_entero(env, VAR_ADMIN_POR_MINUTO, defaults.admin_por_minuto),
            acceso_por_minuto=_entero(env, VAR_ACCESO_POR_MINUTO, defaults.acceso_por_minuto),
            key_por_minuto=_entero(env, VAR_KEY_POR_MINUTO, defaults.key_por_minuto),
            tope_diario_global=_entero(env, VAR_TOPE_DIARIO, defaults.tope_diario_global),
        )


def _entero(env: Mapping[str, str], nombre: str, por_defecto: int) -> int:
    texto = env.get(nombre, "").strip()
    if not texto:
        return por_defecto
    try:
        valor = int(texto)
    except ValueError:
        valor = 0
    if valor <= 0:
        raise LimitesConfigError(f"{nombre} debe ser un entero positivo (recibido: {texto!r}).")
    return valor


def _ahora_utc() -> datetime:
    return datetime.now(timezone.utc)


class PresupuestoDiario:
    """Cuenta los tokens gastados en el día UTC, en total y por tenant, contra sus topes."""

    def __init__(self, tope_global: int, ahora: Callable[[], datetime] = _ahora_utc) -> None:
        self._tope_global = tope_global
        # Se inyecta para simular el cambio de día en los tests.
        self._ahora = ahora
        self._lock = threading.Lock()
        self._dia: date = ahora().date()
        self._global = 0
        self._por_tenant: dict[str, int] = {}

    def _rodar_dia(self) -> None:
        """Si cambió el día UTC, reinicia los contadores. Llamar con el lock tomado."""
        hoy = self._ahora().date()
        if hoy != self._dia:
            self._dia, self._global, self._por_tenant = hoy, 0, {}

    def _segundos_hasta_manana(self) -> float:
        ahora = self._ahora()
        manana = datetime.combine(ahora.date() + timedelta(days=1), datetime.min.time(), tzinfo=timezone.utc)
        return (manana - ahora).total_seconds()

    def verificar(self, tenant: TenantConfig) -> None:
        """Lanza PresupuestoAgotado si el día ya llegó al tope global o al del tenant."""
        with self._lock:
            self._rodar_dia()
            gastado_tenant = self._por_tenant.get(tenant.tenant_id, 0)
            tope_tenant = tenant.tope_diario_tokens
            if self._global >= self._tope_global or (tope_tenant is not None and gastado_tenant >= tope_tenant):
                raise PresupuestoAgotado(self._segundos_hasta_manana())

    def registrar(self, tenant_id: str, tokens: int) -> None:
        """Suma los tokens de una llamada ya hecha."""
        with self._lock:
            self._rodar_dia()
            self._global += tokens
            self._por_tenant[tenant_id] = self._por_tenant.get(tenant_id, 0) + tokens

    def gastado_hoy(self) -> int:
        with self._lock:
            self._rodar_dia()
            return self._global


class EstadoLimites:
    """Todos los límites del proceso juntos: se crea una vez y la API lo recibe por inyección."""

    def __init__(self, limites: Limites) -> None:
        self.limites = limites
        self.chat = LimitadorVentana(limites.chat_por_minuto, 60)
        self.foundry = LimitadorVentana(limites.foundry_por_hora, 3600)
        self.admin = LimitadorVentana(limites.admin_por_minuto, 60)
        self.acceso = LimitadorVentana(limites.acceso_por_minuto, 60)
        # La clave del limitador es el key_id: una key compartida entre varias IPs igual tiene un tope propio.
        self.por_key = LimitadorVentana(limites.key_por_minuto, 60)
        self.presupuesto = PresupuestoDiario(limites.tope_diario_global)


class GeneradorConPresupuesto:
    """Decorador de un GeneradorTexto: antes de cada llamada al LLM aplica los límites, y después anota los tokens.

    Cumple el mismo Protocol que el generador real, así app/rag/generate.py no se entera
    (patrón Decorator; en .NET sería una clase que envuelve a `ITextGenerator`). Como el
    control está acá, solo cuentan las llamadas que de verdad van al LLM: las consultas
    bajo el umbral (que cuestan 0) nunca llegan a esta clase.
    """

    def __init__(
        self,
        interno: GeneradorTexto,
        estado: EstadoLimites,
        tenant: TenantConfig,
        ip: str,
    ) -> None:
        self._interno = interno
        self._estado = estado
        self._tenant = tenant
        self._ip = ip

    def generar(self, instrucciones: str, mensaje: str, max_tokens_salida: int = MAX_TOKENS_SALIDA) -> ChatResult:
        # Primero el tope diario (no consume cupo de la IP si el servicio ya está cortado).
        self._estado.presupuesto.verificar(self._tenant)
        espera = self._estado.foundry.intentar(self._ip)
        if espera is not None:
            raise LimiteExcedido(espera)

        resultado = self._interno.generar(instrucciones, mensaje, max_tokens_salida)
        self._estado.presupuesto.registrar(self._tenant.tenant_id, resultado.tokens_total)
        return resultado
