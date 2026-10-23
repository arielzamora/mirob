"""Configuración de Azure AI Foundry, leída de variables de entorno.

Equivale a una clase de opciones de .NET (IOptions<FoundryOptions>) con
validación al arrancar: si falta algo, falla de inmediato con un mensaje
claro (fail-fast) en vez de explotar más tarde con un error críptico.
"""

import os
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Self

from app.env import cargar_env

# Nombres de las variables de entorno, definidos una sola vez para no repetir
# strings sueltos por el código (como un `const string` en C#).
VAR_ENDPOINT = "FOUNDRY_ENDPOINT"
VAR_CHAT = "FOUNDRY_CHAT_DEPLOYMENT"
VAR_EMBEDDINGS = "FOUNDRY_EMBEDDINGS_DEPLOYMENT"


class FoundryConfigError(ValueError):
    """La configuración de Foundry está incompleta o es inválida."""

    # Hereda de ValueError: es como definir una excepción propia que extiende
    # ArgumentException en C#. Sin cuerpo: alcanza con el mensaje que le pasemos.


# frozen=True hace la instancia inmutable (no se pueden reasignar campos), igual
# que un `record` de C# o una clase con propiedades `init`. Es lo recomendable
# para configuración: se crea una vez y nadie la modifica por accidente.
@dataclass(frozen=True)
class FoundrySettings:
    """Datos para conectarse a un proyecto de Foundry."""

    # URL del proyecto: https://<recurso>.services.ai.azure.com/api/projects/<proyecto>
    endpoint: str
    # Nombre del DEPLOYMENT de chat (el que ponés vos en el portal), no el del modelo.
    # `responses.create(model=...)` recibe este nombre.
    chat_deployment: str
    # Deployment de embeddings. Opcional: todavía no se usa (Etapa 2.1).
    embeddings_deployment: str | None = None

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Self:
        """Arma la configuración desde variables de entorno y la valida.

        `env` permite pasar un diccionario propio (los tests lo usan para no
        depender de tu máquina). Si es None, se lee un archivo .env (si existe)
        y después las variables reales del sistema.
        """
        # `classmethod` es como un método estático de fábrica en C# (static Create()):
        # recibe la clase (`cls`) en vez de una instancia.
        if env is None:
            # cargar_env() copia las líneas CLAVE=valor de backend/.env a
            # os.environ (ver app/env.py). No pisa variables ya definidas en el
            # sistema; si el archivo no existe, no hace nada.
            cargar_env()
            # os.environ se comporta como un Mapping[str, str] (como
            # Environment.GetEnvironmentVariables() en .NET).
            env = os.environ

        # Leemos cada variable; _leer devuelve "" si falta o está en blanco.
        endpoint = _leer(env, VAR_ENDPOINT)
        chat_deployment = _leer(env, VAR_CHAT)
        # `or None`: un string vacío es "falso" en Python, así que queda en None.
        embeddings_deployment = _leer(env, VAR_EMBEDDINGS) or None

        # Juntamos TODAS las obligatorias que faltan, para avisar de una sola vez
        # (mejor que arreglar una, volver a correr y que falte la siguiente).
        faltantes = [
            nombre
            for nombre, valor in ((VAR_ENDPOINT, endpoint), (VAR_CHAT, chat_deployment))
            if not valor
        ]
        if faltantes:
            raise FoundryConfigError(
                f"Faltan variables de entorno: {', '.join(faltantes)}. "
                "Copiá backend/.env.example a backend/.env y completalo."
            )

        # El SDK usa Entra ID sobre HTTPS; un endpoint sin https:// es un error de copiado.
        if not endpoint.startswith("https://"):
            raise FoundryConfigError(f"{VAR_ENDPOINT} debe empezar con https:// (recibido: {endpoint!r}).")

        # rstrip("/") saca la barra final: el SDK le agrega "/openai/v1" al endpoint
        # y una barra de más dejaría "//" en la URL.
        return cls(
            endpoint=endpoint.rstrip("/"),
            chat_deployment=chat_deployment,
            embeddings_deployment=embeddings_deployment,
        )


def _leer(env: Mapping[str, str], nombre: str) -> str:
    """Valor de la variable sin espacios alrededor, o "" si no existe."""
    # .get(clave, valor_por_defecto) no lanza KeyError si falta (a diferencia de env[clave]).
    return env.get(nombre, "").strip()
