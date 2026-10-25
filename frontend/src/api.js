// Cliente HTTP mínimo para la API de MiRob. Sin axios ni librerías extra:
// fetch nativo alcanza.

// En desarrollo apunta a la API local. Para otro entorno se define VITE_API_BASE_URL
// (Vite lee las variables VITE_* de frontend/.env* al compilar; en .NET sería
// como una URL base en appsettings.{Environment}.json).
const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000";

function extraerDetalle(data) {
  // FastAPI devuelve `detail` como string (HTTPException), como una lista de errores de
  // validación de pydantic ([{msg, loc, ...}, ...]) o, en el acceso por access key, como un
  // objeto { codigo, mensaje }. Normalizamos los tres casos a un solo string legible.
  if (Array.isArray(data?.detail)) {
    return data.detail.map((error) => error.msg).join(", ");
  }
  if (data?.detail && typeof data.detail === "object") {
    return data.detail.mensaje;
  }
  return data?.detail;
}

// Arma el Error de una respuesta fallida. Además del mensaje lleva el `codigo` estable del backend
// (key_vencida, sesion_vencida...) y el status, para que la pantalla decida qué mostrar sin
// interpretar textos (en .NET sería una excepción propia con propiedades).
function errorDeRespuesta(response, data, mensajePorDefecto) {
  const error = new Error(extraerDetalle(data) || mensajePorDefecto);
  error.codigo = data?.detail?.codigo ?? null;
  error.status = response.status;
  return error;
}

async function leerJson(response) {
  // Si la API está caída o un proxy responde HTML, response.json() tira; en ese
  // caso devolvemos null y el llamador muestra su mensaje genérico.
  try {
    return await response.json();
  } catch {
    return null;
  }
}

export async function getTenants() {
  const response = await fetch(`${API_BASE_URL}/api/v1/tenants`);
  const data = await leerJson(response);

  if (!response.ok) {
    throw new Error(extraerDetalle(data) || "No se pudo cargar la lista de tenants.");
  }

  return data; // [{ tenant_id, nombre, acceso }]
}

// Canjea una access key por una sesión corta. La key viaja UNA vez, en el cuerpo (nunca en la URL).
export async function postAcceso(tenantId, key) {
  const response = await fetch(`${API_BASE_URL}/api/v1/acceso`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ tenant_id: tenantId, key }),
  });

  const data = await leerJson(response);

  if (!response.ok) {
    throw errorDeRespuesta(response, data, "No se pudo verificar la access key.");
  }

  return data; // { tenant_id, session_token, expires_in, etiqueta, key_expires_at }
}

// El límite de 350 caracteres de `message` y el de 1000 por mensaje del historial
// los valida el backend (422); el front los respeta para no mandar requests inválidos.
// `sessionToken` solo se manda en los tenants que exigen access key.
export async function postChat({ tenantId, message, mode, conversationHistory, sessionToken }) {
  const headers = { "Content-Type": "application/json" };
  if (sessionToken) {
    headers.Authorization = `Bearer ${sessionToken}`;
  }

  const response = await fetch(`${API_BASE_URL}/api/v1/chat`, {
    method: "POST",
    headers,
    body: JSON.stringify({
      tenant_id: tenantId,
      message,
      mode,
      conversation_history: conversationHistory,
    }),
  });

  const data = await leerJson(response);

  if (!response.ok) {
    throw errorDeRespuesta(response, data, "Error al consultar el chat.");
  }

  return data;
}

// --- Administración de access keys (todas exigen la API key del admin en X-API-Key) ---------------

async function pedirAdmin(ruta, apiKey, opciones, mensajePorDefecto) {
  const headers = { "X-API-Key": apiKey };
  if (opciones.body) {
    headers["Content-Type"] = "application/json";
  }
  const response = await fetch(`${API_BASE_URL}${ruta}`, { ...opciones, headers });
  const data = await leerJson(response);
  if (!response.ok) {
    throw errorDeRespuesta(response, data, mensajePorDefecto);
  }
  return data;
}

// Las keys de un tenant, con estado y contadores de uso (nunca la key ni su hash).
export function getAdminKeys(apiKey, tenantId) {
  const consulta = new URLSearchParams({ tenant_id: tenantId });
  return pedirAdmin(`/admin/keys?${consulta}`, apiKey, { method: "GET" }, "No se pudieron cargar las keys.");
}

// Crea una key. La respuesta trae la key en claro y el enlace: es la ÚNICA vez que se ven.
export function postAdminKey(apiKey, { tenantId, etiqueta, diasVigencia, cupoPreguntas }) {
  const cuerpo = { tenant_id: tenantId, etiqueta, dias_vigencia: diasVigencia, cupo_preguntas: cupoPreguntas };
  return pedirAdmin(
    "/admin/keys",
    apiKey,
    { method: "POST", body: JSON.stringify(cuerpo) },
    "No se pudo crear la key.",
  );
}

export function postRevocarKey(apiKey, keyId) {
  return pedirAdmin(
    `/admin/keys/${encodeURIComponent(keyId)}/revoke`,
    apiKey,
    { method: "POST" },
    "No se pudo revocar la key.",
  );
}

export async function postAdminContent(file, apiKey, tenantId) {
  // multipart/form-data: no seteamos el header Content-Type a mano, el
  // browser arma el boundary correcto solo cuando el body es un FormData.
  const formData = new FormData();
  formData.append("tenant_id", tenantId);
  formData.append("file", file);

  const response = await fetch(`${API_BASE_URL}/admin/content`, {
    method: "POST",
    // El admin exige la API key en este header (solución temporal, ver
    // docs/adr/0002-api-key-temporal-admin.md). Solo Content-Type queda libre.
    headers: { "X-API-Key": apiKey },
    body: formData,
  });

  const data = await leerJson(response);

  if (!response.ok) {
    throw new Error(extraerDetalle(data) || "Error al subir el contenido.");
  }

  return data;
}
