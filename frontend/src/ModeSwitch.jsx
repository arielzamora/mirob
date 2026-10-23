import { MODOS } from "./modos.js";
import "./ModeSwitch.css";

// Switch segmentado (un grupo de radios con aspecto de botones). Los radios dan
// navegación con flechas y lectura correcta en lectores de pantalla sin código extra.
function ModeSwitch({ value, onChange, disabled }) {
  return (
    <fieldset className="mode-switch" disabled={disabled}>
      <legend className="mode-switch__leyenda">Modo de respuesta</legend>
      {MODOS.map((modo) => (
        <label
          key={modo.id}
          className={`mode-switch__opcion ${value === modo.id ? "is-active" : ""}`}
          title={modo.ayuda}
        >
          <input
            type="radio"
            name="modo-respuesta"
            value={modo.id}
            checked={value === modo.id}
            onChange={() => onChange(modo.id)}
          />
          {modo.label}
        </label>
      ))}
    </fieldset>
  );
}

export default ModeSwitch;
