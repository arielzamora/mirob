import "./RobotEspera.css";

// Robot animado para las esperas del chat (el backend puede tardar unos segundos si
// estaba "dormido": ver minReplicas en infra/main.bicep). Es un SVG propio, sin librerías:
// en .NET sería un componente de presentación sin lógica, solo markup + estilo.
//
// `texto`  mensaje visible debajo del robot.
// `pequeno` versión compacta, para ir dentro de una burbuja.
function RobotEspera({ texto, pequeno = false }) {
  return (
    <div className={`robot-espera ${pequeno ? "robot-espera--pequeno" : ""}`} role="status">
      <svg
        className="robot-espera__dibujo"
        viewBox="0 0 48 48"
        width={pequeno ? 28 : 48}
        height={pequeno ? 28 : 48}
        aria-hidden="true"
      >
        {/* Antena: la bolita titila para dar sensación de "pensando" */}
        <line x1="24" y1="4" x2="24" y2="10" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
        <circle className="robot-espera__antena" cx="24" cy="4" r="2.5" fill="currentColor" />
        {/* Cabeza */}
        <rect x="8" y="10" width="32" height="26" rx="8" fill="none" stroke="currentColor" strokeWidth="2.5" />
        {/* Ojos: parpadean */}
        <circle className="robot-espera__ojo" cx="18" cy="23" r="3" fill="currentColor" />
        <circle className="robot-espera__ojo" cx="30" cy="23" r="3" fill="currentColor" />
        {/* Orejas */}
        <line x1="4" y1="22" x2="4" y2="28" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" />
        <line x1="44" y1="22" x2="44" y2="28" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" />
        {/* Cuello */}
        <line x1="24" y1="36" x2="24" y2="42" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" />
      </svg>

      <span className="robot-espera__texto">
        {texto}
        {/* Tres puntitos que suben y bajan en secuencia */}
        <span className="robot-espera__puntos" aria-hidden="true">
          <span />
          <span />
          <span />
        </span>
      </span>
    </div>
  );
}

export default RobotEspera;
