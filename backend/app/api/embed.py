"""GET /embed/{tenant_id}: la página que un sitio externo embebe en su <iframe>.

Por qué existe: qué sitios pueden embeber el widget se controla con el header
`Content-Security-Policy: frame-ancestors ...`, y ese header lo manda la página que se
embebe. El front es un hosting estático con un header fijo para todos los tenants, así que
no puede distinguir "digital-zur" de "ariel-zamora". Esta ruta SÍ puede: responde un HTML
mínimo con el `frame-ancestors` de ESE tenant (su `origenes_permitidos`), y adentro carga el
front con `?tenant=<id>&embed=1`. El front, a su vez, solo permite ser embebido por esta API
(ver docs/adr/0003-multitenancy-y-aislamiento.md).
"""

import os
from html import escape
from urllib.parse import quote

from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse

from app.api.dependencias import resolver_tenant
from app.origenes import validar_origen

VAR_FRONTEND_URL = "MIROB_FRONTEND_URL"

router = APIRouter()

_PAGINA = """<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{titulo}</title>
<style>html,body{{height:100%;margin:0}}iframe{{display:block;width:100%;height:100%;border:0}}</style>
</head>
<body>
<iframe src="{src}" title="{titulo}"></iframe>
</body>
</html>
"""


def _origen_del_front() -> str:
    """Origen del front, de MIROB_FRONTEND_URL. Lanza 503 si no está configurado o es inválido."""
    valor = os.environ.get(VAR_FRONTEND_URL, "").strip().rstrip("/")
    try:
        return validar_origen(valor)
    except ValueError as error:
        raise HTTPException(
            status_code=503,
            detail=f"La vista embebida no está configurada: falta un {VAR_FRONTEND_URL} válido en el servidor.",
        ) from error


@router.get("/embed/{tenant_id}", response_class=HTMLResponse)
def embed(tenant_id: str) -> HTMLResponse:
    """HTML con el iframe del front y el frame-ancestors del tenant."""
    tenant = resolver_tenant(tenant_id)
    front = _origen_del_front()

    # Sin orígenes permitidos el tenant no se puede embeber en ningún sitio ('none').
    ancestros = " ".join(tenant.origenes_permitidos) or "'none'"
    # default-src 'none': esta página no carga nada salvo el iframe del front y su CSS en línea.
    politica = (
        f"default-src 'none'; style-src 'unsafe-inline'; frame-src {front}; frame-ancestors {ancestros}"
    )

    # tenant_id ya está validado ([a-z0-9-]); quote/escape son defensa extra al armar HTML y URL.
    src = f"{front}/?tenant={quote(tenant.tenant_id)}&embed=1"
    html = _PAGINA.format(src=escape(src, quote=True), titulo=escape(f"Asistente de {tenant.nombre}", quote=True))

    return HTMLResponse(
        content=html,
        headers={
            "Content-Security-Policy": politica,
            "X-Content-Type-Options": "nosniff",
            "Referrer-Policy": "no-referrer",
        },
    )
