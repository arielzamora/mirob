"""Lab 2b — Diagnóstico de embeddings: ¿por qué da 404 y por qué dirección sí se puede llamar?

Correr desde backend/ con el venv activado y `az login` hecho:
    python -m labs.lab02b_embeddings_diagnostico

Hace dos cosas, ambas de solo lectura o casi gratis:
  1. Lista los deployments que ve el PROYECTO de Foundry (nombre, modelo y tipo), para confirmar que el de embeddings existe
     con el nombre que está en FOUNDRY_EMBEDDINGS_DEPLOYMENT.
  2. Pide el embedding de una sola palabra ("hola", unos pocos tokens) por varias direcciones posibles y muestra, de cada
     una, si funcionó o el código y el cuerpo exacto del error. Así se ve cuál sirve sin adivinar.

No imprime tokens ni secretos: solo nombres de deployments, direcciones públicas y mensajes de error.
"""

from urllib.parse import urlparse

from azure.core.exceptions import AzureError
from azure.identity import get_bearer_token_provider
from openai import APIStatusError, OpenAI, OpenAIError

from app.foundry.client import crear_cliente_proyecto, crear_credencial
from app.foundry.config import FoundrySettings

ALCANCE_FOUNDRY = "https://ai.azure.com/.default"
ALCANCE_COGNITIVE = "https://cognitiveservices.azure.com/.default"


def listar_deployments(proyecto) -> None:
    print("1) Deployments que ve el proyecto:")
    try:
        encontrados = list(proyecto.deployments.list())
    except AzureError as error:
        print(f"   No se pudo listar ({type(error).__name__}): {str(error)[:300]}")
        return
    if not encontrados:
        print("   (ninguno)")
    for d in encontrados:
        # `name` es el nombre del DEPLOYMENT: el que va en FOUNDRY_EMBEDDINGS_DEPLOYMENT.
        print(f"   - name={d.name!r} modelo={getattr(d, 'model_name', None)!r} tipo={getattr(d, 'type', None)!r}")


def probar(etiqueta: str, cliente: OpenAI, deployment: str) -> bool:
    """Una llamada mínima de embeddings. Devuelve True si funcionó."""
    print(f"\n- {etiqueta}")
    print(f"  base_url: {cliente.base_url}")
    try:
        respuesta = cliente.embeddings.create(model=deployment, input=["hola"], timeout=30)
    except APIStatusError as error:
        cuerpo = (error.response.text or "").strip()[:400] or "(sin cuerpo)"
        print(f"  FALLO {error.status_code}: {cuerpo}")
        return False
    except OpenAIError as error:
        print(f"  FALLO {type(error).__name__}: {str(error)[:300]}")
        return False
    print(f"  OK: {len(respuesta.data[0].embedding)} dimensiones, {respuesta.usage.total_tokens} tokens")
    return True


def main() -> None:
    settings = FoundrySettings.from_env()
    deployment = settings.embeddings_deployment
    if not deployment:
        print("Falta FOUNDRY_EMBEDDINGS_DEPLOYMENT en backend/.env")
        return

    host = urlparse(settings.endpoint).netloc  # <recurso>.services.ai.azure.com
    recurso = host.split(".")[0]
    print(f"Endpoint del proyecto: {settings.endpoint}")
    print(f"Deployment de embeddings configurado: {deployment!r}\n")

    credencial = crear_credencial()
    with crear_cliente_proyecto(settings, credencial) as proyecto:
        listar_deployments(proyecto)

        print("\n2) Pruebas de embeddings (una palabra cada una):")
        funcionaron = []
        # A) Lo que usa MiRob hoy: <endpoint del proyecto>/openai/v1 con el token de ai.azure.com.
        if probar("A) Ruta del PROYECTO (la actual de MiRob)", proyecto.get_openai_client(), deployment):
            funcionaron.append("A")
        # B) Ruta del RECURSO (sin /api/projects/...), con el mismo token que el chat.
        base_recurso = f"https://{host}/openai/v1"
        if probar("B) Ruta del RECURSO, token ai.azure.com", proyecto.get_openai_client(base_url=base_recurso), deployment):
            funcionaron.append("B")
        # C) Ruta del RECURSO con el token de Cognitive Services (el estándar de Azure OpenAI).
        cliente_c = OpenAI(base_url=base_recurso, api_key=get_bearer_token_provider(credencial, ALCANCE_COGNITIVE))
        if probar("C) Ruta del RECURSO, token cognitiveservices", cliente_c, deployment):
            funcionaron.append("C")
        # D) El dominio clásico de Azure OpenAI del mismo recurso, con el token de Cognitive Services.
        cliente_d = OpenAI(
            base_url=f"https://{recurso}.openai.azure.com/openai/v1",
            api_key=get_bearer_token_provider(credencial, ALCANCE_COGNITIVE),
        )
        if probar("D) Dominio openai.azure.com, token cognitiveservices", cliente_d, deployment):
            funcionaron.append("D")

    print(f"\nResumen: funcionaron las opciones {funcionaron or 'NINGUNA'}. Pasame esta salida completa.")


# Solo corre main() si ejecutás este archivo directamente (no si lo importan).
if __name__ == "__main__":
    main()
