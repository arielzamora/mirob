# Imagen del backend de MiRob. Build desde backend/:  docker build -t mirob-backend .
FROM python:3.14-slim

# PYTHONUNBUFFERED: los logs salen al instante (Azure los lee de stdout).
# PYTHONDONTWRITEBYTECODE: no genera .pyc dentro de la imagen.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Primero solo requirements.txt: Docker cachea esta capa y no reinstala todo si solo cambia el código
# (como el restore de NuGet antes de copiar el resto en un Dockerfile de .NET).
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY app ./app
COPY tenants ./tenants
COPY data/sample ./data/sample

# Usuario sin privilegios. El contenido real de los tenants se monta en /app/data/tenants
# (Azure Files); se crea vacío acá para que exista también al correr la imagen sin mount.
RUN useradd --uid 1000 --create-home appuser \
    && mkdir -p data/tenants \
    && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

# Una sola réplica con un worker: el índice vive en memoria (ver ADR 0003).
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
