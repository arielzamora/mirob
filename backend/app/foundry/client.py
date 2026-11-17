"""Creación de los clientes del SDK de Azure (credencial, proyecto y OpenAI).

SOLO esta carpeta (app/foundry/) puede importar azure.* u openai: el resto de
MiRob no debe saber que existe el SDK (igual que en .NET aislarías el
HttpClient/SDK detrás de una interfaz).

Las firmas están verificadas contra los paquetes instalados (azure-ai-projects
2.4.0, openai 2.54.0).
"""

from urllib.parse import urlparse

from azure.ai.projects import AIProjectClient
from azure.identity import DefaultAzureCredential
from openai import OpenAI

from app.foundry.config import FoundrySettings


def crear_credencial() -> DefaultAzureCredential:
    """Paso 1 — Credencial de Entra ID, sin API keys.

    DefaultAzureCredential prueba en orden varias formas de autenticarse; en tu
    máquina usa la sesión de `az login`, y en Azure usará la Managed Identity sin
    cambiar código. En .NET es exactamente la misma clase: DefaultAzureCredential.
    Tu usuario necesita el rol "Foundry User" (antes "Azure AI User") sobre el proyecto.
    """
    return DefaultAzureCredential()


# Alcance (scope) del token de Entra ID para Foundry. Verificado en el paquete instalado
# (azure/ai/projects/_patch.py): es el que usa get_openai_client() para armar su token.
ALCANCE_FOUNDRY = "https://ai.azure.com/.default"


def pedir_token(credencial: DefaultAzureCredential) -> None:
    """Pide (y deja en la caché de la credencial) el token de Entra ID, SIN llamar al modelo.

    La primera vez DefaultAzureCredential prueba varios mecanismos hasta dar con uno que
    funcione (en Azure, la Managed Identity) y eso tarda segundos. Hacerlo al arrancar evita
    que lo pague la primera pregunta de una persona. No cuesta tokens del modelo.
    """
    credencial.get_token(ALCANCE_FOUNDRY)


def crear_cliente_proyecto(settings: FoundrySettings, credencial: DefaultAzureCredential) -> AIProjectClient:
    """Paso 2 — Cliente del proyecto de Foundry.

    Firma verificada (azure-ai-projects 2.4.0):
        AIProjectClient(endpoint: str, credential: TokenCredential, *, allow_preview=False, **kwargs)
    Es sincrónico y no hace red al construirse (la primera llamada de red
    ocurre al usarlo). Soporta `with` para cerrarse solo, como IDisposable.
    """
    return AIProjectClient(endpoint=settings.endpoint, credential=credencial)


def obtener_cliente_openai(proyecto: AIProjectClient) -> OpenAI:
    """Paso 3 — Cliente de OpenAI ya autenticado contra el proyecto.

    Firma verificada: AIProjectClient.get_openai_client(*, agent_name=None, **kwargs) -> openai.OpenAI
    Devuelve un cliente OpenAI estándar con base_url = "<endpoint>/openai/v1" y el
    token de Entra ID como api_key (se renueva solo), así que no manejás ninguna
    key. Con este cliente se usa la API Responses: `cliente.responses.create(...)`
    (ver chat.py).
    """
    return proyecto.get_openai_client()


def url_openai_del_recurso(endpoint_del_proyecto: str) -> str:
    """La URL base de la API OpenAI a nivel del RECURSO de Foundry, a partir del endpoint del proyecto.

        https://<recurso>.services.ai.azure.com/api/projects/<proyecto>  ->  https://<recurso>.services.ai.azure.com/openai/v1

    Se queda solo con el host: el recurso es el padre del proyecto, y esa ruta sirve a todos los deployments del recurso.
    """
    return f"https://{urlparse(endpoint_del_proyecto).netloc}/openai/v1"


def obtener_cliente_openai_recurso(proyecto: AIProjectClient, endpoint_del_proyecto: str) -> OpenAI:
    """Cliente de OpenAI contra la ruta del RECURSO, con el mismo token de Entra ID que el cliente del chat.

    Por qué existe: la ruta del proyecto (`<endpoint>/openai/v1`, la de `obtener_cliente_openai`) sirve el chat con la
    API Responses pero responde **404 sin cuerpo** a `/embeddings`. Verificado con labs/lab02b_embeddings_diagnostico.py:
    la ruta del recurso sirve embeddings (1536 dimensiones con text-embedding-3-small) usando el MISMO token
    (alcance https://ai.azure.com/.default), así que no hace falta otra credencial.

    Firma verificada en el paquete instalado (azure-ai-projects 2.4.0): get_openai_client(*, agent_name=None, **kwargs)
    acepta `base_url` y lo usa en lugar de la ruta por defecto.
    """
    return proyecto.get_openai_client(base_url=url_openai_del_recurso(endpoint_del_proyecto))
