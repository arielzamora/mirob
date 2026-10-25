import "./AccesoScreen.css";

// Pantalla para quien llega a un asistente con access key sin una sesión válida:
// no abrió el enlace, la key no sirvió (vencida, revocada, inválida) o la sesión venció.
// Es solo presentación: la lógica vive en useAcceso.js.
//
// `error` = { codigo, mensaje } cuando hubo un motivo concreto; null si simplemente falta la key.
function AccesoScreen({ tenantNombre, error }) {
  const hayMotivo = error !== null;

  return (
    <section className="acceso-screen" aria-label={`Acceso a ${tenantNombre}`}>
      <svg className="acceso-screen__candado" viewBox="0 0 24 24" width="40" height="40" aria-hidden="true">
        <rect x="4" y="10" width="16" height="11" rx="3" fill="none" stroke="currentColor" strokeWidth="2" />
        <path d="M8 10V7a4 4 0 0 1 8 0v3" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
      </svg>

      <h2 className="acceso-screen__titulo">
        {hayMotivo ? "No pudimos darte acceso" : `El asistente de ${tenantNombre} es de acceso privado`}
      </h2>

      {/* role="alert": el lector de pantalla anuncia el motivo apenas aparece */}
      <p className="acceso-screen__texto" role={hayMotivo ? "alert" : undefined}>
        {hayMotivo
          ? error.mensaje
          : "Para conversar necesitás una access key. Abrí el enlace que te enviaron: ya la incluye."}
      </p>
    </section>
  );
}

export default AccesoScreen;
