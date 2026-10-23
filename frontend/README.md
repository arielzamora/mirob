# MiRob — frontend

React + Vite. Dos pantallas:

- **Chat** (`src/ChatScreen.jsx`): pregunta, historial en estado local y un **switch de modo** (`Raw` / `Humanizado` / `Foundry`, `src/ModeSwitch.jsx`). Cada respuesta muestra el score, el umbral del tenant, el costo en tokens (modo Foundry) y los chunks recuperados (`src/ChatMeta.jsx`) para auditar el retrieval.
- **Carga de contenido** (`src/AdminScreen.jsx`): una caja para subir un `.md` o `.txt` al tenant elegido.

El **selector de tenant** está en el header y se alimenta de `GET /api/v1/tenants`. Al cambiar de tenant el chat se reinicia (nunca se mezclan conversaciones) y la URL queda con `?tenant=...`.

El cliente HTTP está en `src/api.js`. Por defecto espera la API en `http://localhost:8000`; para otra URL definí `VITE_API_BASE_URL` (por ejemplo en `frontend/.env.local`, que no se versiona).
Ver el [README principal](../README.md) para levantar la API.

## Parámetros de la URL

| Parámetro | Efecto |
|---|---|
| `?tenant=digital-zur` | Tenant inicial. Si falta, se usa el primero de la lista. |
| `&embed=1` | **Vista pública**: sin pestañas, sin selector de tenant ni de modo, sin detalle de auditoría. Modo `foundry` por defecto. Sirve para el `<iframe>` del sitio y como enlace directo. |
| `&mode=raw\|humanized\|foundry` | Modo inicial (en `embed`, por defecto `foundry`; si no, `raw`). |

Ejemplo de widget para un sitio institucional:

```html
<iframe
  src="https://<tu-api>/embed/digital-zur"
  title="Asistente de Digital Zur"
  style="width: 100%; max-width: 420px; height: 560px; border: 0"
></iframe>
```

> El sitio embebe la URL **de la API** (`/embed/<tenant>`), no el front directo: esa página fija qué sitios pueden embeberla (`origenes_permitidos` del tenant) y carga este front con `?tenant=...&embed=1`. Para que funcione hacen falta tres cosas: el sitio en los `origenes_permitidos` del tenant, el origen de este front en `MIROB_CORS_ORIGINS` de la API, y que el hosting del front mande `Content-Security-Policy: frame-ancestors <origen de la API>`. Ver [ADR 0003](../docs/adr/0003-multitenancy-y-aislamiento.md).

```bash
npm install
npm run dev     # http://localhost:5173
npm run lint    # oxlint
npm run build   # build de producción en dist/
```
