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
| `foundry` | Respuesta redactada por un modelo chico de Azure AI Foundry (gpt-5.4-nano o similar) a partir de los chunks recuperados; solo se llama si la similitud supera el umbral del tenant. |

## Arquitectura

```
backend/
  app/
    rag/
      ingest.py      # pandas: lee .md/.txt y arma el DataFrame de chunks
      embeddings.py  # TfidfVectorizer ajustado sobre el corpus
      retrieve.py    # coseno a mano con numpy + top-k
      humanize.py    # modo "humanized" (aperturas y limpieza de formato)
      generate.py    # modo "foundry": umbral, armado del prompt y llamada al LLM
    api/chat.py      # POST /api/v1/chat (tenant_id, message, mode)
    api/routes.py    # POST /admin/content y POST /admin/reindex (reciben tenant_id)
    api/health.py    # GET /health (probes del contenedor y smoke test)
    api/tenants.py   # GET /api/v1/tenants (id y nombre de los tenants habilitados)
    api/embed.py     # GET /embed/{tenant_id}: página para el iframe, con frame-ancestors del tenant
    finops.py        # rate limit por IP, tope diario de tokens y el decorador del generador
    ratelimit.py     # limitador de ventana deslizante en memoria
    origenes.py      # validación de orígenes y CORS
    tenants.py       # registro de tenants: validación del id y configuración
    chunk_format.py  # extrae header y respuesta de un chunk
    reindex.py       # un índice en memoria por tenant y su reindexado
    main.py          # app FastAPI + CORS
    foundry/         # único que usa el SDK de Azure: client.py, chat.py, generador.py (+ tipos.py sin SDK)
  labs/              # un script por módulo del curso AI-103 (lab01_smoke.py)
  tenants/           # config versionada de cada tenant: tenant.json + prompt.md
  data/              # contenido real (ignorado por git)
    tenants/<id>/    # los .md/.txt de cada tenant
    sample/          # perfil ficticio versionado (tenant "demo" y tests)
  tests/             # pytest
frontend/            # React + Vite: chat y pantalla de carga
docs/adr/            # decisiones de arquitectura (10 ADR)
docs/arquitectura/   # diagramas C4 en Mermaid
infra/               # Bicep: Container Apps, Azure Files, Static Web Apps (ver abajo)
```

Flujo de `POST /api/v1/chat`: `tenant_id` + `message` → vectorización TF-IDF → coseno contra la matriz **de ese tenant** → top 3 chunks → respuesta según `mode`.
Flujo de `POST /admin/content`: `tenant_id` + archivo `.md`/`.txt` → se guarda en `backend/data/tenants/<tenant_id>/` → se reindexa solo ese tenant.

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
pip install -r requirements-dev.txt   # solo para correr la API: requirements.txt
python -m uvicorn app.main:app --reload --port 8000
```

La documentación interactiva queda en <http://localhost:8000/docs>. Prueba rápida:

```bash
curl -X POST http://localhost:8000/api/v1/chat -H "Content-Type: application/json" \
  -d '{"tenant_id": "demo", "message": "Que idiomas hablas?"}'
```

### Frontend

```bash
cd frontend
npm install
npm run dev
```

Abre <http://localhost:5173>. El front espera la API en `http://localhost:8000` (se cambia con `VITE_API_BASE_URL`).
Tiene selector de tenant y switch de modo (Raw / Humanizado / Foundry); con `?tenant=<id>&embed=1` muestra la vista pública para embeber en un iframe (ver [frontend/README.md](frontend/README.md)).

### Habilitar la carga de contenido (admin)

1. Copiá `backend/.env.example` a `backend/.env` y poné una clave en `MIROB_ADMIN_API_KEY`
   (la variable explica cómo generar una).
2. Reiniciá la API. En la pantalla **Cargar contenido** del front se pide esa clave
   (queda solo en memoria, se pierde al recargar). Por línea de comandos:

```bash
curl -X POST http://localhost:8000/admin/content -H "X-API-Key: <tu-clave>" -F "tenant_id=ariel-zamora" -F "file=@mi-contenido.md"
```

### Chat: `POST /api/v1/chat`

```json
{ "tenant_id": "demo", "message": "¿Qué idiomas hablás?", "mode": "raw", "conversation_history": [] }
```

- `message`: obligatorio, hasta 350 caracteres. `conversation_history` es opcional (`[{role: "user"|"assistant", content}]`, hasta 20 mensajes).
- `mode`: `raw` (el chunk top-1 tal cual, **sin cortar por umbral**, para auditar el retrieval), `humanized` (top-1 con apertura conversacional, sin LLM) o `foundry` (respuesta redactada por el LLM, ver abajo).
- La respuesta incluye `answer`, `chunks` (top 3 con `score`), `top_score`, `threshold` (del tenant), `below_threshold`, `llm_called`, `usage` (tokens; en 0 si no se llamó al LLM) y, cuando hubo LLM, `latency_ms` y `deployment`.

**Modo `foundry` y ahorro de tokens:**

1. Se busca localmente (TF-IDF + coseno) solo en los chunks del tenant.
2. Si el mejor score es **menor al umbral del tenant** (`umbral_similitud`), **no se llama a Foundry**: se responde con un mensaje fijo ("No encontré información sobre esa consulta...") y el gasto es 0 tokens.
3. Si lo supera, al LLM viajan como máximo 3 chunks (solo los que superan el umbral), delimitados entre `<contexto>` y `</contexto>` y tratados como datos; más los últimos 6 mensajes del historial, recortados.
4. El LLM usa el `prompt.md` del tenant como instrucciones y está limitado a `max_output_tokens=250`.
   - Cada `prompt.md` fija una **frase exacta** para "no tengo esa información" y prohíbe suponer capacidades, sectores o casos que no figuren de forma explícita. Probado con el LLM real: una pregunta sobre un sector que el contenido no menciona ("¿hacen sistemas para hospitales?") responde esa frase en lugar de decir que sí.

El umbral por defecto es 0.10 porque TF-IDF da scores más bajos que un modelo de embeddings (con las stopwords en español filtradas, las preguntas bien respondidas quedan entre ~0.15 y 0.80 y las fuera de tema en ~0). Calibralo por tenant en su `tenant.json` con preguntas reales de su contenido.

Errores del modo foundry: sin configurar → 503; falla del modelo (credenciales, red, cuota) → 502 con un mensaje genérico. Los modos `raw` y `humanized`, y las consultas bajo el umbral, no necesitan Foundry.

**Configurar Foundry** (Entra ID, sin API keys): `az login` con el rol **Foundry User** (antes "Azure AI User") sobre el proyecto, y en `backend/.env` las variables `FOUNDRY_ENDPOINT` y `FOUNDRY_CHAT_DEPLOYMENT` (el nombre de tu deployment). Para probar la conexión sola: `python -m labs.lab01_smoke`.

**Qué modelo usar:** gpt-4o-mini y gpt-4.1-mini figuran como *Deprecated* y una suscripción nueva no puede desplegarlos. Usá `gpt-5.4-nano` (o `gpt-5.4-mini`) con deployment **GlobalStandard** (pago por token, nunca Provisioned). Son modelos de razonamiento: no se les envía `temperature` ni `reasoning` salvo que definas `FOUNDRY_TEMPERATURE` / `FOUNDRY_REASONING_EFFORT` (ver `.env.example`). Si las respuestas vuelven vacías, el modelo gastó el tope de 250 tokens pensando: bajá el esfuerzo (por ejemplo `minimal`). El calendario de retiros de cada modelo está en la [documentación de Microsoft](https://learn.microsoft.com/en-us/azure/foundry/openai/concepts/model-retirement-schedule).

### Tenants (multitenant)

Cada cliente es un *tenant* con su propio contenido, índice y system prompt. Un tenant jamás ve los chunks de otro:
cada uno tiene su índice, armado solo con los archivos de su carpeta.

- **Configuración (versionada):** `backend/tenants/<tenant_id>/tenant.json` (nombre, umbral de similitud, orígenes permitidos, `habilitado`) y `prompt.md` (system prompt propio).
- **Contenido (ignorado por git):** `backend/data/tenants/<tenant_id>/*.md|txt`. Se carga con `POST /admin/content`, o copiando los archivos y llamando a `POST /admin/reindex` (header `X-API-Key`; opcionalmente `tenant_id`, si no reindexa todos).
- **Tenant `demo`:** lee el perfil ficticio de `backend/data/sample/` (solo lectura). Un clon recién bajado responde con `tenant_id: "demo"`.
- **Sin respaldo silencioso:** un tenant sin contenido responde 503; nunca usa el de otro.
- Opcionales en `tenant.json`: `origenes_permitidos` (sitios que pueden embeber el widget) y `tope_diario_tokens` (tope propio del tenant).
- `tenant_id` es obligatorio en `/api/v1/chat` y `/admin/content`: minúsculas, números y guiones. Inexistente o deshabilitado → 404; formato inválido → 422.

Formato esperado en cada archivo (los `.md` con un `##` por tema):

```markdown
## Idiomas
P: ¿Qué idiomas hablás?
R: Español nativo e inglés B2.
```

## Salida a producción: CORS, embebido y límites

Detalle y motivos en [ADR 0003](docs/adr/0003-multitenancy-y-aislamiento.md) y [ADR 0004](docs/adr/0004-gobierno-de-costos-de-ia.md). Variables en `backend/.env.example`.

- **CORS:** lista cerrada. Siempre `localhost:5173/3000`, más los orígenes del front en `MIROB_CORS_ORIGINS`. Es global: quien llama a la API es el front, no el sitio que lo embebe.
- **Embeber el widget:** el sitio del cliente usa `https://<api>/embed/<tenant_id>` (no el front directo). Esa página manda `Content-Security-Policy: frame-ancestors` con los `origenes_permitidos` de **ese** tenant (`'none'` si no tiene) y carga el front en modo público. Requiere `MIROB_FRONTEND_URL`. El front, además, tiene que aceptar como `frame-ancestors` la API **y los sitios de los tenants**: el navegador comprueba todos los ancestros del iframe (el front va dentro de `/embed`, que va dentro del sitio del cliente). El pipeline lo arma solo desde los `tenant.json` (ver [ADR 0003](docs/adr/0003-multitenancy-y-aislamiento.md)).
- **Límites por IP** (429 con `Retry-After`): 20 pedidos de chat por minuto, 30 llamadas al LLM por hora, 10 pedidos al admin por minuto. Detrás de un proxy, definí `MIROB_TRUST_PROXY=1`.
- **Tope diario de tokens:** 200.000 por día UTC en total (y `tope_diario_tokens` opcional por tenant). Al alcanzarlo, solo el modo foundry responde 429.
- **Límite conocido:** los contadores viven en memoria del proceso (por réplica). Con varias réplicas el tope real es mayor.
- **Tenants privados (access keys):** un tenant con `"acceso": "access_key"` en su `tenant.json` solo responde con una sesión. Se entrega un enlace `https://<front>/?tenant=<id>&embed=1#k=<key>` (la key va en el fragmento: no viaja al servidor), el front la canjea una vez por una sesión de 2 horas (`POST /api/v1/acceso`) y el chat usa esa sesión. Cada key tiene etiqueta, vencimiento (14 días por defecto), cupo de preguntas opcional y su propio límite por minuto; se crean, ven y revocan desde la pestaña **Access keys** del front (o `/admin/keys`). Solo se guardan contadores, nunca el texto de las preguntas. Requiere `MIROB_SESSION_SECRET`; sin él un tenant privado responde 503 en lugar de quedar abierto. Detalle y motivos en [ADR 0010](docs/adr/0010-access-keys-de-reclutadores.md).

## Docker, infraestructura y CI/CD

- `backend/Dockerfile` arma la imagen de la API (Python 3.14, usuario sin privilegios). El contenido real **no** entra a la imagen (`backend/.dockerignore`); en Azure se monta desde Azure Files en `/app/data/tenants`. Para probarla en local: `docker build -t mirob-backend backend` (requiere Docker Desktop corriendo).
- `infra/main.bicep` define Container Apps (el backend, escala a cero, una sola réplica), Storage con Azure Files, Static Web Apps (el front), Log Analytics con tope diario y el rol *Foundry User* para la identidad del backend. El recurso de Foundry y su modelo se crearon a mano y solo se **referencian**. Validación local: `az bicep build --file infra/main.bicep`.
- `.github/workflows/ci.yml` corre tests, lint y build del front, validación del Bicep y build de la imagen (en cada pull request). `deploy.yml` lo reutiliza y, **solo si pasa**, publica la imagen en GHCR, despliega el Bicep, publica el front en Static Web Apps y corre un smoke test. Se autentica en Azure con OIDC, sin claves. Preparación y operación: [infra/README.md](infra/README.md).

## Tests

```bash
cd backend
pip install -r requirements-dev.txt   # incluye pytest
python -m pytest -q
```

- Usan **siempre** el perfil ficticio (`data/sample/`) o carpetas temporales, nunca contenido real, así que dan el mismo resultado en cualquier máquina.
- `test_retrieve.py` afirma resultados exactos (*esta pregunta debe traer este chunk como top-1*) y compara el coseno a mano con el de scikit-learn.
- `test_chat.py` prueba el contrato de `/api/v1/chat` (modos, validación, límite de 350 caracteres, corte por umbral con un LLM falso).
- `test_origenes.py`, `test_ratelimit.py`, `test_finops.py`, `test_limites_api.py` y `test_embed.py` prueban CORS, los límites (con relojes falsos) y el `frame-ancestors` por tenant.
- `test_generate.py` y `test_foundry_generador.py` prueban el prompt, el umbral y la capa Foundry con clientes falsos: ningún test usa red ni gasta tokens.
- `test_api.py` llama a la API en memoria con `TestClient` (sin abrir puertos); la subida de archivos escribe en una carpeta temporal. Cubre la autenticación del admin y los intentos de path traversal.
- `test_tenants.py` prueba la validación del `tenant_id`, las configuraciones versionadas y el aislamiento entre tenants.
- `test_uploads.py` prueba la validación de nombres y contenido de los archivos subidos.
- `test_accesskeys.py`, `test_sesiones.py`, `test_acceso.py` y `test_admin_keys.py` prueban las access keys: creación, hash, vencimiento, revocación, cupos atómicos con varios hilos, sesiones firmadas, el chat protegido y los endpoints del admin. Usan relojes falsos y carpetas temporales.

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

Diagramas C4 (contexto, contenedores, componentes y despliegue) y flujos clave: [docs/arquitectura/c4.md](docs/arquitectura/c4.md).

- [ADR 0001 — Retrieval léxico (TF-IDF) como línea base](docs/adr/0001-retrieval-lexico-tfidf.md) (enmendado: stopwords en español)
- [ADR 0002 — API key temporal para el admin](docs/adr/0002-api-key-temporal-admin.md)
- [ADR 0003 — Multitenancy: aislamiento de datos y embebido por tenant](docs/adr/0003-multitenancy-y-aislamiento.md)
- [ADR 0004 — Gobierno de costos de IA](docs/adr/0004-gobierno-de-costos-de-ia.md) (enmendado: modelo y umbral)
- [ADR 0005 — Hosting en Azure: Container Apps, Azure Files y Static Web Apps](docs/adr/0005-hosting-en-azure.md)
- [ADR 0006 — CI/CD con GitHub Actions y OIDC](docs/adr/0006-cicd-github-actions-y-oidc.md)
- [ADR 0007 — Widget embebible y cadena de iframes](docs/adr/0007-widget-embebible-y-cadena-de-iframes.md)
- [ADR 0008 — Foundry: autenticación, modelo y comportamiento del asistente](docs/adr/0008-foundry-autenticacion-modelo-y-prompts.md)
- [ADR 0009 — Contenido de los tenants y reindexado automático](docs/adr/0009-contenido-de-tenants-y-reindexado-automatico.md)
- [ADR 0010 — Acceso de reclutadores con access keys, sesiones firmadas y cupos](docs/adr/0010-access-keys-de-reclutadores.md)
