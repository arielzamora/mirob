# MiRob

Asistente en forma de bot que me representa ante reclutadores: responde preguntas
sobre mi experiencia, proyectos y forma de trabajar a partir de contenido que cargo yo.

> 🚧 **En construcción.** Proyecto de aprendizaje y portfolio, desarrollado por etapas.
> Hoy corre la **Etapa 1**: retrieval léxico hecho a mano, sin LLM.

## Qué hace hoy

1. Leo mi contenido (archivos `.md` y `.txt` con pares pregunta/respuesta) y lo parto en *chunks*.
2. Calculo TF-IDF sobre los chunks y, para cada pregunta, busco el más parecido con **similitud coseno implementada a mano con numpy**.
3. El chat devuelve ese chunk. No hay generación de texto: lo que ves es lo que está en el contenido.

### Modos de respuesta

| Modo | Qué devuelve |
|---|---|
| `raw` (por defecto) | El chunk recuperado, tal cual. |
| `humanized` | Solo la respuesta (`R:`) del chunk, con una apertura conversacional. Es cosmético: no genera contenido nuevo ni cambia el ranking. |
| `foundry` | *Próximamente (Etapa 2).* Respuesta generada con Azure AI Foundry a partir de los chunks recuperados. |

## Arquitectura

```
backend/
  app/
    rag/
      ingest.py      # pandas: lee .md/.txt y arma el DataFrame de chunks
      embeddings.py  # TfidfVectorizer ajustado sobre el corpus
      retrieve.py    # coseno a mano con numpy + top-k
      humanize.py    # modo "humanized" (aperturas y limpieza de formato)
    api/routes.py    # POST /chat y POST /admin/content
    chunk_format.py  # extrae header y respuesta de un chunk
    reindex.py       # índice vigente en memoria y reindexado
    main.py          # app FastAPI + CORS
    foundry/         # único que usa el SDK de Azure (esqueleto, Etapa 2)
  labs/              # un script por módulo del curso AI-103 (lab01_smoke.py)
  data/              # TU contenido real (ignorado por git)
    sample/          # perfil ficticio versionado (respaldo y tests)
  tests/             # pytest
frontend/            # React + Vite: chat y pantalla de carga
docs/adr/            # decisiones de arquitectura
```

Flujo de `POST /chat`: pregunta → vectorización TF-IDF → coseno contra la matriz del corpus → top-1 → respuesta.
Flujo de `POST /admin/content`: archivo `.md`/`.txt` → se guarda en `backend/data/` → se reindexa todo el corpus.

**Chunking:** los `.md` se cortan por headers de nivel 2 (`##`); los `.txt`, por párrafo (línea en blanco).

## Stack

- **Backend:** FastAPI (Python), pandas, numpy y scikit-learn (solo `TfidfVectorizer`; el coseno es propio).
- **Frontend:** React + Vite.
- **Sin base de datos:** el índice vive en memoria y se reconstruye al arrancar.
- **Etapa 2 (planificada):** Azure AI Foundry para embeddings semánticos y generación.

## Cómo correrlo

Desarrollado y probado con Python 3.14 y Node 24 (las dependencias están fijadas en `backend/requirements.txt`).

### Backend

```bash
cd backend
python -m venv .venv
# Windows (PowerShell): .venv\Scripts\Activate.ps1   |   macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
python -m uvicorn app.main:app --reload --port 8000
```

La documentación interactiva queda en <http://localhost:8000/docs>. Prueba rápida:

```bash
curl -X POST http://localhost:8000/chat -H "Content-Type: application/json" \
  -d '{"pregunta": "Que idiomas hablas?"}'
```

### Frontend

```bash
cd frontend
npm install
npm run dev
```

Abre <http://localhost:5173>. El front espera la API en `http://localhost:8000`.

### Habilitar la carga de contenido (admin)

1. Copiá `backend/.env.example` a `backend/.env` y poné una clave en `MIROB_ADMIN_API_KEY`
   (la variable explica cómo generar una).
2. Reiniciá la API. En la pantalla **Cargar contenido** del front se pide esa clave
   (queda solo en memoria, se pierde al recargar). Por línea de comandos:

```bash
curl -X POST http://localhost:8000/admin/content -H "X-API-Key: <tu-clave>" -F "file=@mi-contenido.md"
```

### Qué contenido se indexa

- Si `backend/data/` tiene archivos `.md`/`.txt`, se usan **esos** (tu contenido real; la carpeta está ignorada por git).
- Si no, se usa el perfil ficticio de `backend/data/sample/`. Por eso un clon recién bajado ya responde.

Formato esperado en cada archivo (los `.md` con un `##` por tema):

```markdown
## Idiomas
P: ¿Qué idiomas hablás?
R: Español nativo e inglés B2.
```

## Tests

```bash
cd backend
python -m pytest -q
```

- Usan **siempre** el perfil ficticio (`data/sample/`), nunca contenido real, así que dan el mismo resultado en cualquier máquina.
- `test_retrieve.py` afirma resultados exactos (*esta pregunta debe traer este chunk como top-1*) y compara el coseno a mano con el de scikit-learn.
- `test_api.py` llama a la API en memoria con `TestClient` (sin abrir puertos); la subida de archivos escribe en una carpeta temporal. Cubre la autenticación del admin y los intentos de path traversal.
- `test_uploads.py` prueba la validación de nombres y contenido de los archivos subidos.

## Limitaciones conocidas

- ⚠️ **`POST /admin/content` se protege con una única API key compartida** (header `X-API-Key`, variable `MIROB_ADMIN_API_KEY`). Es una solución **temporal** ([ADR 0002](docs/adr/0002-api-key-temporal-admin.md)): sin rate limiting, sin rotación y sin identidad por usuario. Se reemplazará por autenticación propia (ver roadmap).
- Si `MIROB_ADMIN_API_KEY` no está configurada, el admin queda **deshabilitado** (503) en vez de abierto.
- Los archivos subidos se validan: nombre sin carpetas ni caracteres raros, extensión `.md`/`.txt`, UTF-8 y hasta 1 MB. El nombre no se "limpia": si es inválido se rechaza (422).
- El retrieval es **léxico**: depende de que la pregunta comparta palabras con el chunk. Las preguntas con sinónimos o parafraseadas pueden fallar; ver [ADR 0001](docs/adr/0001-retrieval-lexico-tfidf.md).

## Roadmap

| Etapa | Alcance | Estado |
|---|---|---|
| 1 | Ingesta de .txt/.md, retrieval léxico (TF-IDF + coseno), API y chat en React | En curso |
| 2 | Azure AI Foundry: embeddings semánticos, búsqueda híbrida, generación con citas, evals y observabilidad con OpenTelemetry | Planificada |
| 3 | Acceso de reclutadores con access keys, deploy en Azure Container Apps y Static Web Apps, CI/CD | Planificada |

## Principio de diseño

MiRob habla en mi nombre, así que nunca debe inventar: si el contenido cargado no
tiene la respuesta, lo dice. Cada etapa se mide con evals contra ese criterio.

## Decisiones de arquitectura

- [ADR 0001 — Retrieval léxico (TF-IDF) como línea base](docs/adr/0001-retrieval-lexico-tfidf.md)
- [ADR 0002 — API key temporal para el admin](docs/adr/0002-api-key-temporal-admin.md)
