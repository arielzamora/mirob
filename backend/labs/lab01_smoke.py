"""Lab 1 — Smoke test: ¿puedo hablar con mi deployment de Foundry?

Correr desde backend/ con el venv activado:
    python -m labs.lab01_smoke

(-m ejecuta el módulo como parte del paquete `labs`, así los imports `from app...`
funcionan igual que en la app; es como correr un proyecto de consola de la solución.)

Antes de correrlo:
  1. Creá el proyecto y un deployment de chat en el portal de Foundry (lo creás vos).
  2. `az login` en esta máquina, con el rol "Foundry User" (antes "Azure AI User") sobre el proyecto.
  3. Copiá backend/.env.example a backend/.env y completá los valores.

Usa las TRES funciones de app/foundry/client.py y `generar` de app/foundry/chat.py,
que ya están implementadas (la API usa las mismas a través de FoundryGenerador).
"""

from app.foundry.chat import generar
from app.foundry.client import crear_cliente_proyecto, crear_credencial, obtener_cliente_openai
from app.foundry.config import FoundrySettings


def main() -> None:
    # Paso 0 — Config (ya implementada): lee .env y falla con un mensaje claro si falta algo.
    settings = FoundrySettings.from_env()
    print(f"Endpoint: {settings.endpoint}")
    print(f"Deployment de chat: {settings.chat_deployment}")

    # Paso 1 — Credencial (client.py).
    credencial = crear_credencial()

    # Paso 2 — Cliente del proyecto (client.py). Va en un `with` para
    # que se cierre solo al terminar, como un `using` en C#.
    with crear_cliente_proyecto(settings, credencial) as proyecto:
        # Paso 3 — Cliente OpenAI autenticado (client.py).
        cliente = obtener_cliente_openai(proyecto)

        # Paso 4 — Primera llamada al modelo (`generar` en chat.py).
        resultado = generar(
            cliente,
            settings,
            instrucciones="Respondé en una sola frase, en español.",
            mensaje="Decime hola y contame qué modelo sos.",
        )

    print(f"\nRespuesta: {resultado.texto}")
    print(
        f"Tokens: {resultado.tokens_entrada} entrada + {resultado.tokens_salida} salida "
        f"= {resultado.tokens_total} | Latencia: {resultado.latencia_ms:.0f} ms"
    )


# Solo corre main() si ejecutás este archivo directamente (no si lo importan),
# el equivalente al `static void Main` de C#.
if __name__ == "__main__":
    main()
