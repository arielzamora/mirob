import "./Entrada.css";

// Lo que ve quien abre la URL del front sin un enlace de acceso: nada del panel de administración.
// Los reclutadores entran por su enlace (…/?tenant=<id>&embed=1#k=<key>) y el widget de un sitio por
// /embed/<tenant>: ninguno de los dos pasa por acá.
function Portada() {
  return (
    <div className="entrada">
      <h1 className="entrada__marca">MiRob</h1>
      <p className="entrada__texto">
        Asistente que responde preguntas sobre un perfil profesional. Para conversar con él, abrí el
        enlace que te enviaron.
      </p>
    </div>
  );
}

export default Portada;
