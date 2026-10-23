"""GET /health: ¿el proceso está vivo? Lo usan las probes del contenedor y el smoke test del pipeline.

Es a propósito lo más simple posible: no toca disco, ni Foundry, ni el índice. Si respondiera
según el estado de Foundry, una caída del modelo haría que Azure reinicie el contenedor en
bucle sin que eso arregle nada (el chat en modo raw seguiría funcionando).
"""

from fastapi import APIRouter

router = APIRouter()


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
