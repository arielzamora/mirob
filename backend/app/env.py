"""Carga del archivo backend/.env, compartida por toda la app.

Se define en un único lugar para que la API (clave de admin) y foundry/
(endpoint y deployments) lean el mismo archivo, sin importar desde qué
carpeta se ejecute el programa.
"""

from pathlib import Path

from dotenv import load_dotenv

# backend/app/env.py -> parents[0]=app/, parents[1]=backend/ -> backend/.env
RUTA_ENV = Path(__file__).resolve().parents[1] / ".env"


def cargar_env() -> None:
    """Copia las líneas CLAVE=valor de backend/.env a las variables de entorno.

    Es el equivalente a que .NET lea appsettings.json / User Secrets al arrancar
    y los exponga como configuración. No pisa variables que ya estén definidas
    en el sistema, y si el archivo no existe simplemente no hace nada.
    """
    # load_dotenv(ruta) modifica os.environ del proceso; no devuelve nada útil acá.
    load_dotenv(RUTA_ENV)
