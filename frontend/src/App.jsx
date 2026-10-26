import { useEffect, useState } from "react";
import "./App.css";
import { getTenants } from "./api.js";
import ChatConAcceso from "./ChatConAcceso.jsx";
import AdminLogin from "./AdminLogin.jsx";
import AdminScreen from "./AdminScreen.jsx";
import KeysScreen from "./KeysScreen.jsx";
import Portada from "./Portada.jsx";
import { MODOS } from "./modos.js";

// Pocas pantallas (ver CLAUDE.md: nada de router todavía, sería
// sobrekill para esto). El tab activo es estado simple de React.
const TABS = [
  { id: "chat", label: "Chat" },
  { id: "admin", label: "Cargar contenido" },
  { id: "keys", label: "Access keys" },
];

// Parámetros de la URL, leídos una sola vez al cargar la página:
//   ?tenant=digital-zur   tenant a usar (si falta, el primero de la lista)
//   &embed=1              vista pública: sin pestañas, selector de tenant ni modo.
//                         Sirve para el <iframe> del sitio y también como enlace directo.
//   &mode=raw|humanized|foundry   modo inicial (en embed, por defecto "foundry")
//   &header=0             en embed, sin la barra superior (el sitio que lo embebe pone la suya)
// Sin embed=1 el front es el panel de administración: queda oculto tras un minilogin (la clave de admin,
// validada por la API) y solo se llega a ese login con #admin al final de la URL. Quien abre la URL sin nada
// de eso ve una portada neutra.
const parametros = new URLSearchParams(window.location.search);
const EMBED = parametros.get("embed") === "1";
const SIN_BARRA = EMBED && parametros.get("header") === "0";
const TENANT_DE_LA_URL = parametros.get("tenant") ?? "";
const modoPedido = parametros.get("mode");
const MODO_INICIAL = MODOS.some((modo) => modo.id === modoPedido)
  ? modoPedido
  : EMBED
    ? "foundry"
    : "raw";

function App() {
  const [tabActivo, setTabActivo] = useState("chat");
  const [tenants, setTenants] = useState([]); // [{ tenant_id, nombre }]
  const [errorTenants, setErrorTenants] = useState("");
  const [tenantId, setTenantId] = useState(TENANT_DE_LA_URL);
  // false hasta que la API responde (bien o mal) al primer pedido: si estaba "dormida",
  // ese pedido es el que la despierta y el chat muestra el robot de espera mientras tanto.
  const [apiLista, setApiLista] = useState(false);
  // API key del admin: la comparten "Cargar contenido" y "Access keys" para no tipearla dos veces.
  // Vive solo en memoria (nunca en localStorage ni en la URL): al recargar la página se pierde.
  const [apiKeyAdmin, setApiKeyAdmin] = useState("");
  // true cuando la API aceptó la clave de admin: recién entonces se muestra el panel.
  const [adminDesbloqueado, setAdminDesbloqueado] = useState(false);
  // true si la URL termina en #admin: muestra el minilogin en lugar de la portada.
  const [pideAdmin, setPideAdmin] = useState(window.location.hash === "#admin");

  useEffect(() => {
    const alCambiarElHash = () => setPideAdmin(window.location.hash === "#admin");
    window.addEventListener("hashchange", alCambiarElHash);
    return () => window.removeEventListener("hashchange", alCambiarElHash);
  }, []);

  // Carga la lista de tenants habilitados al abrir la página.
  useEffect(() => {
    let cancelado = false;
    getTenants()
      .then((lista) => {
        if (cancelado) return;
        setApiLista(true);
        setTenants(lista);
        // Sin tenant en la URL (o con uno que no está en la lista), se parte del primero de la
        // lista. En la vista pública no se adivina: se respeta lo que diga la URL.
        setTenantId((actual) => {
          if (EMBED) return actual;
          return lista.some((tenant) => tenant.tenant_id === actual) ? actual : (lista[0]?.tenant_id ?? "");
        });
      })
      .catch((error) => {
        if (cancelado) return;
        // Aun con error se deja de mostrar "conectando": si la API falla, que el chat
        // muestre su propio error al enviar en vez de quedarse esperando para siempre.
        setApiLista(true);
        setErrorTenants(error.message);
      });
    return () => {
      cancelado = true;
    };
  }, []);

  function handleCambiarTenant(evento) {
    const nuevo = evento.target.value;
    setTenantId(nuevo);
    // Se refleja en la URL (sin recargar) para que F5 o copiar el enlace conserven el tenant.
    const url = new URL(window.location.href);
    url.searchParams.set("tenant", nuevo);
    window.history.replaceState(null, "", url);
  }

  const tenantActual = tenants.find((tenant) => tenant.tenant_id === tenantId);
  const tenantNombre = tenantActual?.nombre ?? tenantId;

  function renderContenido() {
    if (!tenantId) {
      return (
        <p className="app-aviso">
          {EMBED
            ? "Falta indicar el tenant en la URL (?tenant=...)."
            : errorTenants || "Cargando tenants..."}
        </p>
      );
    }
    if (tabActivo === "admin" && !EMBED) {
      return (
        <AdminScreen
          key={tenantId}
          tenantId={tenantId}
          tenantNombre={tenantNombre}
          apiKey={apiKeyAdmin}
          onApiKeyChange={setApiKeyAdmin}
        />
      );
    }
    if (tabActivo === "keys" && !EMBED) {
      return (
        <KeysScreen
          key={tenantId}
          tenantId={tenantId}
          tenantNombre={tenantNombre}
          tenantAcceso={tenantActual?.acceso}
          apiKey={apiKeyAdmin}
          onApiKeyChange={setApiKeyAdmin}
        />
      );
    }
    // key={tenantId}: al cambiar de tenant el chat se remonta y empieza sin historial.
    // Si el tenant exige access key (tenantActual.acceso), ChatConAcceso muestra la pantalla de acceso
    // hasta que haya una sesión válida.
    return (
      <ChatConAcceso
        key={tenantId}
        tenantId={tenantId}
        tenantNombre={tenantNombre}
        requiereKey={tenantActual?.acceso === "access_key"}
        tenantListo={apiLista}
        embed={EMBED}
        modoInicial={MODO_INICIAL}
      />
    );
  }

  function handleEntrarAdmin(clave) {
    setApiKeyAdmin(clave);
    setAdminDesbloqueado(true);
    // Se saca el #admin de la URL: ya no hace falta y no queda a la vista.
    const url = new URL(window.location.href);
    url.hash = "";
    window.history.replaceState(null, "", url);
  }

  // Sin embed y sin la clave de admin validada no se muestra el panel: portada o minilogin.
  if (!EMBED && !adminDesbloqueado) {
    return (
      <div className="app">
        <main className="app-main">{pideAdmin ? <AdminLogin onEntrar={handleEntrarAdmin} /> : <Portada />}</main>
      </div>
    );
  }

  return (
    <div className={`app ${EMBED ? "app--embed" : ""}`}>
      {!SIN_BARRA && (
      <header className="app-header">
        {EMBED ? (
          <span className="app-header__brand">{tenantNombre || "Asistente"}</span>
        ) : (
          <>
            <span className="app-header__brand">MiRob</span>

            <label className="app-header__tenant">
              <span className="app-header__tenant-label">Tenant</span>
              <select value={tenantId} onChange={handleCambiarTenant} disabled={tenants.length === 0}>
                {tenants.map((tenant) => (
                  <option key={tenant.tenant_id} value={tenant.tenant_id}>
                    {tenant.nombre} ({tenant.tenant_id})
                  </option>
                ))}
              </select>
            </label>

            <nav className="app-header__tabs" aria-label="Navegación principal">
              {TABS.map((tab) => (
                <button
                  key={tab.id}
                  type="button"
                  className={`app-header__tab ${tabActivo === tab.id ? "is-active" : ""}`}
                  aria-current={tabActivo === tab.id ? "page" : undefined}
                  onClick={() => setTabActivo(tab.id)}
                >
                  {tab.label}
                </button>
              ))}
            </nav>
          </>
        )}
      </header>
      )}

      {errorTenants && tenantId && !EMBED && (
        <p className="app-aviso app-aviso--error" role="alert">
          No se pudo cargar la lista de tenants: {errorTenants}
        </p>
      )}

      <main className="app-main">{renderContenido()}</main>
    </div>
  );
}

export default App;
