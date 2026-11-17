"""Lab 2 — Smoke test de embeddings: ¿puedo hablar con mi deployment de embeddings de Foundry?

Correr desde backend/ con el venv activado:
    python -m labs.lab02_embeddings_smoke

Antes de correrlo:
  1. Creá a mano, en el portal de Foundry y en el MISMO proyecto que el chat, un deployment de un modelo de
     embeddings chico (lo creás vos; el código solo lo referencia por su nombre).
  2. Poné ese nombre en backend/.env: FOUNDRY_EMBEDDINGS_DEPLOYMENT=<nombre-del-deployment>
  3. `az login` con el rol "Foundry User" sobre el proyecto (el mismo que usa el chat). No hace falta ninguna API key.

Hace UNA llamada con tres frases cortas (unas decenas de tokens, centavos de dólar o menos) y muestra:
  - las dimensiones del vector y los tokens que facturó;
  - qué tan cerca queda "celular" de "teléfono" frente a una frase sin relación. Si el modelo entiende el
    significado, la primera similitud tiene que ser claramente mayor (con TF-IDF las dos darían 0).

Usa FoundryEmbeddings (app/foundry/embeddings.py), la misma clase que usa la API, y el cliente compartido con el chat.
"""

from app.foundry.embeddings import FoundryEmbeddings
from app.foundry.generador import FoundryGenerador
from app.rag.semantico import normalizar, similitudes

FRASES = ["¿Me pasás su celular?", "¿Cuál es su teléfono?", "La receta lleva harina, huevos y azúcar."]


def main() -> None:
    # El cliente (y su credencial de Entra ID) se crea una sola vez y se comparte con el chat.
    generador = FoundryGenerador()
    _, settings = generador.obtener_cliente()
    print(f"Endpoint: {settings.endpoint}")
    print(f"Deployment de embeddings: {settings.embeddings_deployment}")

    embeddings = FoundryEmbeddings(generador.obtener_cliente)
    resultado = embeddings.embeber(FRASES)

    vectores = normalizar(resultado.vectores)
    print(f"\nDimensiones del vector: {vectores.shape[1]}")
    print(f"Tokens: {resultado.tokens} | Latencia: {resultado.latencia_ms:.0f} ms | Deployment: {resultado.deployment}")

    # Con vectores normalizados, la similitud coseno es el producto punto.
    similares = similitudes(vectores[0], vectores[1:2])[0]
    distintas = similitudes(vectores[0], vectores[2:3])[0]
    print(f"\n'celular' vs 'teléfono'      : {similares:.3f}")
    print(f"'celular' vs 'receta de cocina': {distintas:.3f}")
    print("OK: el modelo distingue el significado." if similares > distintas else "Revisá: no separa bien los significados.")


# Solo corre main() si ejecutás este archivo directamente (no si lo importan).
if __name__ == "__main__":
    main()
