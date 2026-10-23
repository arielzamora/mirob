# MiRob — frontend

React + Vite. Dos pantallas:

- **Chat** (`src/ChatScreen.jsx`): pregunta, selector de modo (`raw` / `humanized`) e historial en estado local.
- **Carga de contenido** (`src/AdminScreen.jsx`): una caja para subir un `.md` o `.txt`.

El cliente HTTP está en `src/api.js` y espera la API en `http://localhost:8000`
(ver el [README principal](../README.md) para levantarla).

```bash
npm install
npm run dev     # http://localhost:5173
npm run lint    # oxlint
npm run build   # build de producción en dist/
```
