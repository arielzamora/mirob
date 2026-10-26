import { useId, useState } from "react";
import "./Entrada.css";
import { getAdminKeys } from "./api.js";
import RobotEspera from "./RobotEspera.jsx";

// Minilogin del panel de administración: se pide la clave de admin y SOLO si la API la acepta se muestran
// las pantallas. La comprobación la hace el servidor (un pedido a /admin/keys que responde 200 únicamente con
// la clave correcta): nada se compara en el navegador, así que ninguna clave queda escrita en el JavaScript
// que baja cualquiera. En .NET sería un [Authorize] del lado del servidor, no un if en la vista.
//
// La clave no se guarda (ni en localStorage ni en la URL): vive en el estado de App y se pierde al recargar.
function AdminLogin({ onEntrar }) {
  const [clave, setClave] = useState("");
  const [verificando, setVerificando] = useState(false);
  const [mensaje, setMensaje] = useState("");
  const idClave = useId();

  async function handleSubmit(evento) {
    evento.preventDefault();
    if (!clave || verificando) return;
    setVerificando(true);
    setMensaje("");
    try {
      await getAdminKeys(clave);
      onEntrar(clave);
    } catch (falla) {
      // 401 = clave incorrecta. Cualquier otro caso (429 por demasiados intentos, 503, red) muestra su propio mensaje.
      setMensaje(falla.status === 401 ? "Clave incorrecta." : falla.message);
      setVerificando(false);
    }
  }

  return (
    <div className="entrada">
      <h1 className="entrada__marca">MiRob</h1>
      <form className="entrada__form" onSubmit={handleSubmit}>
        <label htmlFor={idClave} className="entrada__label">
          Clave de administración
        </label>
        <input
          id={idClave}
          type="password"
          autoComplete="current-password"
          autoFocus
          value={clave}
          onChange={(evento) => setClave(evento.target.value)}
          disabled={verificando}
        />
        <button type="submit" disabled={!clave || verificando}>
          Entrar
        </button>
        {verificando && <RobotEspera pequeno texto="Verificando" />}
        {mensaje && (
          <p className="entrada__error" role="alert">
            {mensaje}
          </p>
        )}
      </form>
    </div>
  );
}

export default AdminLogin;
