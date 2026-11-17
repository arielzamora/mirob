import { MODOS } from "./modos.js";
import "./ModeSwitch.css";

// Switch segmentado (un grupo de radios con aspecto de botones). Los radios dan
// navegación con flechas y lectura correcta en lectores de pantalla sin código extra.
// Sirve para cualquier lista de opciones [{ id, label, ayuda }]: por defecto los modos de respuesta; el
// retrieval lo reutiliza con su propia lista, leyenda y `grupo` (el `name` de los radios: tiene que ser
// distinto en cada switch de la pantalla para que no se pisen entre sí).
function ModeSwitch({ value, onChange, disabled, opciones = MODOS, leyenda = "Modo de respuesta", grupo = "modo-respuesta" }) {
  return (
    <fieldset className="mode-switch" disabled={disabled}>
      <legend className="mode-switch__leyenda">{leyenda}</legend>
      {opciones.map((opcion) => (
        <label
          key={opcion.id}
          className={`mode-switch__opcion ${value === opcion.id ? "is-active" : ""}`}
          title={opcion.ayuda}
        >
          <input
            type="radio"
            name={grupo}
            value={opcion.id}
            checked={value === opcion.id}
            onChange={() => onChange(opcion.id)}
          />
          {opcion.label}
        </label>
      ))}
    </fieldset>
  );
}

export default ModeSwitch;
