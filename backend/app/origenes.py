"""Orígenes web (https://sitio.com) permitidos: validación y CORS.

Un "origen" es esquema + host + puerto, sin ruta ni barra final. Se valida con una
lista blanca de caracteres porque estos valores terminan dentro de headers HTTP
(CORS y Content-Security-Policy): un valor con ";" o espacios podría inyectar otras
directivas en el header.
"""

import re
from collections.abc import Mapping

# https://host[:puerto] (o http:// solo para localhost, en desarrollo). Sin comodines.
_ORIGEN_VALIDO = re.compile(r"https://[a-z0-9]([a-z0-9.-]*[a-z0-9])?(:\d{1,5})?|http://localhost(:\d{1,5})?")

VAR_CORS_ORIGINS = "MIROB_CORS_ORIGINS"

# Orígenes de desarrollo local (Vite y CRA). Siempre permitidos para poder probar en tu máquina.
ORIGENES_DESARROLLO = ("http://localhost:5173", "http://localhost:3000")


def validar_origen(origen: str) -> str:
    """Devuelve el origen en minúsculas si es válido; si no, lanza ValueError."""
    normalizado = origen.strip().lower()
    if not _ORIGEN_VALIDO.fullmatch(normalizado):
        raise ValueError(
            f"Origen inválido: {origen!r}. Usá https://dominio[:puerto], sin barra final ni comodines."
        )
    return normalizado


def origenes_cors(env: Mapping[str, str]) -> list[str]:
    """Orígenes que pueden llamar a la API desde un navegador: los de desarrollo + MIROB_CORS_ORIGINS.

    MIROB_CORS_ORIGINS es una lista separada por comas con el/los orígenes donde está
    hospedado el front (por ejemplo "https://mirob.azurestaticapps.net"). Ojo: CORS mira el
    origen del FRONT, no el del sitio que embebe el iframe (eso lo controla /embed/{tenant}).
    """
    extra = [valor for valor in env.get(VAR_CORS_ORIGINS, "").split(",") if valor.strip()]
    # dict.fromkeys saca duplicados conservando el orden.
    return list(dict.fromkeys([*ORIGENES_DESARROLLO, *(validar_origen(o) for o in extra)]))
