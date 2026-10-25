import AccesoScreen from "./AccesoScreen.jsx";
import ChatScreen from "./ChatScreen.jsx";
import RobotEspera from "./RobotEspera.jsx";
import { useAcceso } from "./useAcceso.js";

// Decide qué se ve según el acceso del tenant: el chat (si es público o ya hay sesión), la pantalla
// de acceso (si falta la key o no sirvió) o una espera mientras se verifica la key del enlace.
// App lo monta con `key={tenantId}`: al cambiar de tenant el acceso y el chat arrancan limpios.
function ChatConAcceso({ tenantId, tenantNombre, requiereKey, tenantListo, embed, modoInicial }) {
  const acceso = useAcceso(tenantId, tenantListo, requiereKey);

  if (acceso.fase === "canjeando") {
    return (
      <div className="app-espera">
        <RobotEspera texto="Verificando tu acceso" />
      </div>
    );
  }

  if (acceso.fase === "requerida" || acceso.fase === "error") {
    return <AccesoScreen tenantNombre={tenantNombre} error={acceso.error} />;
  }

  return (
    <ChatScreen
      tenantId={tenantId}
      tenantNombre={tenantNombre}
      embed={embed}
      modoInicial={modoInicial}
      conectando={!tenantListo}
      sessionToken={acceso.sessionToken}
      onAccesoPerdido={acceso.perderAcceso}
    />
  );
}

export default ChatConAcceso;
