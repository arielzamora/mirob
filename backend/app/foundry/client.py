"""Creación de los clientes del SDK de Azure (credencial, proyecto y OpenAI).

SOLO esta carpeta (app/foundry/) puede importar azure.* u openai: el resto de
MiRob no debe saber que existe el SDK (igual que en .NET aislarías el
HttpClient/SDK detrás de una interfaz).

Las firmas están verificadas contra los paquetes instalados (azure-ai-projects
2.4.0, openai 2.54.0).
"""

from azure.ai.projects import AIProjectClient
from azure.identity import DefaultAzureCredential
from openai import OpenAI

from app.foundry.config import FoundrySettings


def crear_credencial() -> DefaultAzureCredential:
    """Paso 1 — Credencial de Entra ID, sin API keys.

    DefaultAzureCredential prueba en orden varias formas de autenticarse; en tu
    máquina usa la sesión de `az login`, y en Azure usará la Managed Identity sin
    cambiar código. En .NET es exactamente la misma clase: DefaultAzureCredential.
    Tu usuario necesita el rol "Azure AI User" sobre el proyecto.
    """
    return DefaultAzureCredential()


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
