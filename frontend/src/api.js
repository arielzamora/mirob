// Cliente HTTP mínimo para la API de MiRob. Sin axios ni librerías extra:
// esta etapa solo necesita dos POST, fetch nativo alcanza.

const API_BASE_URL = "http://localhost:8000";

function extraerDetalle(data) {
  // FastAPI devuelve `detail` como string (HTTPException) o como una lista
  // de errores de validación de pydantic ([{msg, loc, ...}, ...]).
  // Normalizamos ambos casos a un solo string legible.
  if (Array.isArray(data?.detail)) {
    return data.detail.map((error) => error.msg).join(", ");
  }
  return data?.detail;
}

export async function postChat(pregunta, modo) {
  const response = await fetch(`${API_BASE_URL}/chat`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ pregunta, modo }),
  });

  const data = await response.json();

  if (!response.ok) {
    throw new Error(extraerDetalle(data) || "Error al consultar el chat.");
  }

  return data;
}

export async function postAdminContent(file, apiKey) {
  // multipart/form-data: no seteamos el header Content-Type a mano, el
  // browser arma el boundary correcto solo cuando el body es un FormData.
  const formData = new FormData();
  formData.append("file", file);

  const response = await fetch(`${API_BASE_URL}/admin/content`, {
    method: "POST",
    // El admin exige la API key en este header (solución temporal, ver
    // docs/adr/0002-api-key-temporal-admin.md). Solo Content-Type queda libre.
    headers: { "X-API-Key": apiKey },
    body: formData,
  });

  const data = await response.json();

  if (!response.ok) {
    throw new Error(extraerDetalle(data) || "Error al subir el contenido.");
  }

  return data;
}
