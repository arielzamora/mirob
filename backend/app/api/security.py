"""Protección del admin con una API key simple en un header.

SOLUCIÓN TEMPORAL (Etapa 1): una única clave compartida, leída de la variable
de entorno MIROB_ADMIN_API_KEY. La Parte 2.5 del plan la reemplaza por
autenticación propia. Ver docs/adr/0002-api-key-temporal-admin.md.
"""

import os
import secrets

from fastapi import HTTPException, Security
from fastapi.security import APIKeyHeader

# Nombre de la variable de entorno donde vive la clave esperada.
VAR_ADMIN_API_KEY = "MIROB_ADMIN_API_KEY"

# Declara que la clave viaja en el header "X-API-Key". Además de leerlo,
# hace que /docs muestre el botón "Authorize" (como el esquema de seguridad
# de Swagger en ASP.NET Core). auto_error=False: si falta el header NO responde
# solo, así devolvemos nosotros el 401 con nuestro mensaje.
_encabezado_api_key = APIKeyHeader(name="X-API-Key", auto_error=False)


def exigir_api_key(clave_recibida: str | None = Security(_encabezado_api_key)) -> None:
    """Dependencia de FastAPI: deja pasar solo si el header trae la clave correcta.

    Se usa con `dependencies=[Depends(exigir_api_key)]` en la ruta; es el
    equivalente a un filtro [Authorize] / middleware de autorización en .NET.
    Si algo falla, lanza HTTPException y la ruta ni siquiera se ejecuta.
    """
    # Se lee en cada request (no al importar) para que un cambio de entorno,
    # por ejemplo en los tests, se vea de inmediato.
    clave_esperada = os.environ.get(VAR_ADMIN_API_KEY, "").strip()

    # "Fail closed": si el servidor NO tiene clave configurada, el admin queda
    # deshabilitado. Es más seguro que dejarlo abierto por olvido.
    if not clave_esperada:
        raise HTTPException(
            status_code=503,
            detail=f"El admin está deshabilitado: falta configurar {VAR_ADMIN_API_KEY} en el servidor.",
        )

    # compare_digest compara en tiempo constante (no se corta en la primera letra
    # distinta), para no filtrar información por cuánto tarda la respuesta.
    # Se pasan bytes porque compare_digest con str solo acepta ASCII.
    if clave_recibida is None or not secrets.compare_digest(
        clave_recibida.encode("utf-8"), clave_esperada.encode("utf-8")
    ):
        # 401 Unauthorized + WWW-Authenticate es la forma estándar de decir "falta credencial".
        raise HTTPException(
            status_code=401,
            detail="API key inválida o ausente.",
            headers={"WWW-Authenticate": "ApiKey"},
        )
