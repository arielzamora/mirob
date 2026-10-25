import { useCallback, useEffect, useState } from "react";
import { postAcceso } from "./api.js";

// Acceso con access key (Parte 3.0). Este módulo cuida tres cosas:
//   1. Sacar la key del enlace y borrarla de la URL ANTES de que quede en el historial.
//   2. Canjearla UNA vez por una sesión corta (POST /api/v1/acceso).
//   3. Guardar esa sesión en sessionStorage para que un F5 no obligue a abrir el enlace de nuevo.
//
// La key va en el fragmento del enlace (…/?tenant=x&embed=1#k=LA_KEY): el fragmento no se envía al
// servidor, así que no queda en logs ni en el header Referer. Por compatibilidad también se acepta
// ?k=LA_KEY, que se borra igual.

// Códigos de error del backend que significan "ya no tenés acceso": se vuelve a la pantalla de acceso.
export const CODIGOS_ACCESO_PERDIDO = new Set([
  "acceso_requerido",
  "sesion_vencida",
  "key_invalida",
  "key_vencida",
  "key_revocada",
  "cupo_agotado",
]);

const MARGEN_MS = 30_000; // se descarta la sesión guardada 30 s antes de que venza, por las dudas

const claveStorage = (tenantId) => `mirob.sesion.${tenantId}`;

// Se ejecuta UNA vez al cargar la página (nivel de módulo, como un constructor estático): lee y borra la key.
function extraerKeyDelEnlace() {
  const url = new URL(window.location.href);
  const hash = new URLSearchParams(url.hash.replace(/^#/, ""));
  const key = hash.get("k") ?? url.searchParams.get("k");
  if (key !== null) {
    hash.delete("k");
    url.searchParams.delete("k");
    url.hash = hash.toString();
    window.history.replaceState(null, "", url);
  }
  return key ? key.trim() : "";
}

const KEY_DEL_ENLACE = extraerKeyDelEnlace();

// La key del enlace se canjea UNA sola vez por carga de página. Es una variable de módulo (y no
// estado de React) para que tampoco se repita el canje cuando React ejecuta los efectos dos veces
// en desarrollo (StrictMode).
let keyYaCanjeada = false;

function leerSesionGuardada(tenantId) {
  try {
    const texto = window.sessionStorage.getItem(claveStorage(tenantId));
    if (!texto) return null;
    const guardada = JSON.parse(texto);
    if (typeof guardada.token !== "string" || Date.now() >= guardada.expiraEn - MARGEN_MS) return null;
    return guardada;
  } catch {
    return null; // sessionStorage bloqueado (modo privado) o contenido roto: se parte sin sesión
  }
}

function guardarSesion(tenantId, sesion) {
  try {
    window.sessionStorage.setItem(claveStorage(tenantId), JSON.stringify(sesion));
  } catch {
    // Sin sessionStorage la sesión vive solo en memoria: al recargar habrá que abrir el enlace otra vez.
  }
}

function borrarSesion(tenantId) {
  try {
    window.sessionStorage.removeItem(claveStorage(tenantId));
  } catch {
    // nada que borrar
  }
}

// Estado del acceso de un tenant. `fase`:
//   "no_requerida"  el tenant es público: no hay nada que hacer
//   "esperando"     todavía no se sabe si el tenant exige key (la lista de tenants no llegó)
//   "canjeando"     hay una key en el enlace y se está verificando
//   "requerida"     falta la key (se abrió sin enlace, o la sesión venció)
//   "error"         la key no sirvió (inválida, vencida, revocada...); `mensaje` dice por qué
//   "ok"            hay sesión: el chat puede usarla
export function useAcceso(tenantId, tenantListo, requiereKey) {
  const [sesion, setSesion] = useState(() => leerSesionGuardada(tenantId));
  const [error, setError] = useState(null); // { codigo, mensaje }
  const [canjeando, setCanjeando] = useState(false);

  useEffect(() => {
    if (!tenantListo || !requiereKey || sesion || !KEY_DEL_ENLACE || keyYaCanjeada) return;
    keyYaCanjeada = true;
    // setState dentro de una función async (no directo en el efecto): es el patrón recomendado.
    (async () => {
      setCanjeando(true);
      try {
        const respuesta = await postAcceso(tenantId, KEY_DEL_ENLACE);
        const nueva = {
          token: respuesta.session_token,
          etiqueta: respuesta.etiqueta,
          expiraEn: Date.now() + respuesta.expires_in * 1000,
          restantes: respuesta.remaining_questions, // null si la key no tiene cupo
        };
        guardarSesion(tenantId, nueva);
        setSesion(nueva);
      } catch (falla) {
        setError({ codigo: falla.codigo ?? "error", mensaje: falla.message });
      } finally {
        setCanjeando(false);
      }
    })();
  }, [tenantId, tenantListo, requiereKey, sesion]);

  // El chat avisa que el backend rechazó la sesión: se descarta y se muestra el motivo.
  const perderAcceso = useCallback(
    (falla) => {
      borrarSesion(tenantId);
      setSesion(null);
      setError({ codigo: falla.codigo ?? "error", mensaje: falla.message });
    },
    [tenantId],
  );

  // Después de cada respuesta el backend informa cuántas preguntas quedan: se guarda para mostrarlas
  // y para que un F5 no vuelva a mostrar el número del momento de abrir el enlace.
  const actualizarRestantes = useCallback(
    (restantes) => {
      setSesion((actual) => {
        if (!actual) return actual;
        const nueva = { ...actual, restantes };
        guardarSesion(tenantId, nueva);
        return nueva;
      });
    },
    [tenantId],
  );

  let fase;
  if (!tenantListo) fase = "esperando";
  else if (!requiereKey) fase = "no_requerida";
  else if (sesion) fase = "ok";
  else if (canjeando || (KEY_DEL_ENLACE && !keyYaCanjeada && !error)) fase = "canjeando";
  else if (error) fase = "error";
  else fase = "requerida";

  return {
    fase,
    sessionToken: sesion?.token ?? null,
    etiqueta: sesion?.etiqueta ?? "",
    preguntasRestantes: sesion?.restantes ?? null,
    error,
    perderAcceso,
    actualizarRestantes,
  };
}
