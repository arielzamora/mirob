"""Sesiones cortas para quien entró con una access key (Parte 3.0, grupo B).

Flujo: la persona abre su link con la key, el front la manda UNA vez a POST /api/v1/acceso y recibe
una sesión de pocas horas. Desde ahí el chat usa la sesión (header `Authorization: Bearer ...`) y
la key no vuelve a viajar, ni queda en la URL, el historial o los logs.

La sesión es un token firmado SIN estado en el servidor (parecido a un JWT, pero propio y mínimo):
    v1.<datos en base64url>.<firma HMAC-SHA256>
Los datos son { key_id, tenant_id, vencimiento }. Como la firma depende de un secreto que solo
tiene el backend (MIROB_SESSION_SECRET), nadie puede inventar ni alterar una sesión. No se guarda
nada en memoria ni en disco: si el backend se reinicia (scale-to-zero), las sesiones siguen valiendo.

Por qué un token en el header y no una cookie: el front (azurestaticapps.net) y la API
(azurecontainerapps.io) son sitios distintos, y los navegadores bloquean o limitan las cookies entre
sitios (Safari las bloquea siempre). Un token en el header funciona igual en todos.

La sesión NO decide por sí sola: en cada pedido el chat vuelve a mirar que la key siga vigente
(ver ServicioClaves.verificar_vigente), así revocar una key corta el acceso al instante.
"""

import base64
import hashlib
import hmac
import json
import os
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from app.accesskeys import AccessKey

VAR_SECRETO_SESION = "MIROB_SESSION_SECRET"
VAR_MINUTOS_SESION = "MIROB_SESSION_MINUTES"
MINUTOS_SESION_POR_DEFECTO = 120
LARGO_MINIMO_SECRETO = 32
_VERSION = "v1"


class SesionError(Exception):
    """Base de los errores de sesión."""


class SesionInvalida(SesionError):
    """El token está mal formado o su firma no coincide (alterado, o firmado con otro secreto)."""


class SesionExpirada(SesionError):
    """El token es auténtico pero ya pasó su vencimiento."""


class ConfigSesionError(SesionError):
    """Falta o es inválida la configuración de sesiones del servidor (error nuestro, no del cliente)."""


@dataclass(frozen=True)
class Sesion:
    key_id: str
    tenant_id: str
    expira_en: datetime  # UTC


def leer_secreto(env: Mapping[str, str] = os.environ) -> bytes:
    """Secreto de firma, de MIROB_SESSION_SECRET. Sin secreto el acceso por key queda deshabilitado ("fail closed")."""
    secreto = env.get(VAR_SECRETO_SESION, "").strip()
    if len(secreto) < LARGO_MINIMO_SECRETO:
        raise ConfigSesionError(
            f"Falta {VAR_SECRETO_SESION} (de al menos {LARGO_MINIMO_SECRETO} caracteres) en el servidor."
        )
    return secreto.encode("utf-8")


def duracion_de_sesion(env: Mapping[str, str] = os.environ) -> timedelta:
    """Cuánto dura una sesión nueva: MIROB_SESSION_MINUTES, o 2 horas si no se define."""
    texto = env.get(VAR_MINUTOS_SESION, "").strip()
    if not texto:
        return timedelta(minutes=MINUTOS_SESION_POR_DEFECTO)
    try:
        minutos = int(texto)
    except ValueError:
        minutos = 0
    if minutos <= 0:
        raise ConfigSesionError(f"{VAR_MINUTOS_SESION} debe ser un entero positivo (recibido: {texto!r}).")
    return timedelta(minutes=minutos)


def _b64(datos: bytes) -> str:
    return base64.urlsafe_b64encode(datos).rstrip(b"=").decode("ascii")


def _b64_decodificar(texto: str) -> bytes:
    return base64.urlsafe_b64decode(texto + "=" * (-len(texto) % 4))


def _firmar(cuerpo: str, secreto: bytes) -> str:
    return _b64(hmac.new(secreto, f"{_VERSION}.{cuerpo}".encode("ascii"), hashlib.sha256).digest())


def emitir(clave: AccessKey, secreto: bytes, ahora: datetime, duracion: timedelta) -> tuple[str, datetime]:
    """Crea una sesión para la key. Devuelve (token, vencimiento). Nunca dura más que la propia key."""
    expira_en = min(ahora + duracion, clave.vence_en)
    # Se redondea hacia abajo al segundo: el token guarda segundos enteros.
    expira_en = datetime.fromtimestamp(int(expira_en.timestamp()), tz=timezone.utc)
    datos = {"k": clave.key_id, "t": clave.tenant_id, "e": int(expira_en.timestamp())}
    cuerpo = _b64(json.dumps(datos, separators=(",", ":")).encode("utf-8"))
    return f"{_VERSION}.{cuerpo}.{_firmar(cuerpo, secreto)}", expira_en


def leer(token: str, secreto: bytes, ahora: datetime) -> Sesion:
    """Valida firma y vencimiento, y devuelve la sesión. Lanza SesionInvalida o SesionExpirada."""
    partes = (token or "").split(".")
    if len(partes) != 3 or partes[0] != _VERSION:
        raise SesionInvalida("Sesión inválida.")
    _, cuerpo, firma = partes

    # La firma se comprueba ANTES de interpretar los datos, y en tiempo constante.
    if not hmac.compare_digest(firma, _firmar(cuerpo, secreto)):
        raise SesionInvalida("Sesión inválida.")

    try:
        datos = json.loads(_b64_decodificar(cuerpo))
        key_id, tenant_id, expira = str(datos["k"]), str(datos["t"]), int(datos["e"])
    except (ValueError, KeyError, TypeError) as error:
        raise SesionInvalida("Sesión inválida.") from error

    expira_en = datetime.fromtimestamp(expira, tz=timezone.utc)
    if ahora >= expira_en:
        raise SesionExpirada("La sesión venció.")
    return Sesion(key_id=key_id, tenant_id=tenant_id, expira_en=expira_en)
