import { useId, useState } from "react";
import "./KeysScreen.css";
import { getAdminKeys, postAdminKey, postRevocarKey } from "./api.js";

// Administración de access keys de un tenant: crear (con enlace listo para copiar), ver el uso y revocar.
// Habla con /admin/keys, que exige la API key del admin: la recibe de App (vive solo en memoria,
// igual que en la pantalla de carga de contenido) y nunca la guarda.
//
// La key en claro solo existe en la respuesta de "crear": se muestra una vez en un panel y se descarta
// al cerrarlo. El servidor únicamente guarda su hash, así que no hay forma de volver a verla.

const ETIQUETAS_ESTADO = { activa: "Activa", vencida: "Vencida", revocada: "Revocada" };

function fecha(iso) {
  return iso ? new Date(iso).toLocaleString("es-AR", { dateStyle: "short", timeStyle: "short" }) : "—";
}

function textoPreguntas(clave) {
  if (clave.cupo_preguntas === null) return `${clave.preguntas_usadas} (sin cupo)`;
  return `${clave.preguntas_usadas} de ${clave.cupo_preguntas}`;
}

function KeysScreen({ tenantId, tenantNombre, tenantAcceso, apiKey, onApiKeyChange }) {
  const [claves, setClaves] = useState(null); // null = todavía no se cargó
  const [error, setError] = useState("");
  const [cargando, setCargando] = useState(false);

  const [etiqueta, setEtiqueta] = useState("");
  const [dias, setDias] = useState("14");
  const [cupo, setCupo] = useState("");
  const [creando, setCreando] = useState(false);
  const [creada, setCreada] = useState(null); // respuesta de "crear", con la key y el enlace
  const [copiado, setCopiado] = useState("");

  const [confirmando, setConfirmando] = useState(""); // key_id que espera confirmación para revocar
  const [revocando, setRevocando] = useState("");

  const idClaveAdmin = useId();
  const idEtiqueta = useId();
  const idDias = useId();
  const idCupo = useId();

  const requiereKeys = tenantAcceso === "access_key";

  async function cargar() {
    if (!apiKey || cargando) return;
    setCargando(true);
    setError("");
    try {
      setClaves(await getAdminKeys(apiKey, tenantId));
    } catch (falla) {
      setError(falla.message);
    } finally {
      setCargando(false);
    }
  }

  async function crear(evento) {
    evento.preventDefault();
    if (!apiKey || !etiqueta.trim() || creando) return;
    setCreando(true);
    setError("");
    setCreada(null);
    try {
      const nueva = await postAdminKey(apiKey, {
        tenantId,
        etiqueta: etiqueta.trim(),
        diasVigencia: Number(dias) || 14,
        cupoPreguntas: cupo === "" ? null : Number(cupo),
      });
      setCreada(nueva);
      setEtiqueta("");
      setCupo("");
      setClaves(await getAdminKeys(apiKey, tenantId));
    } catch (falla) {
      setError(falla.message);
    } finally {
      setCreando(false);
    }
  }

  async function revocar(keyId) {
    setRevocando(keyId);
    setError("");
    try {
      await postRevocarKey(apiKey, keyId);
      setConfirmando("");
      setClaves(await getAdminKeys(apiKey, tenantId));
    } catch (falla) {
      setError(falla.message);
    } finally {
      setRevocando("");
    }
  }

  async function copiar(texto, etiquetaCopiada) {
    try {
      await navigator.clipboard.writeText(texto);
      setCopiado(etiquetaCopiada);
    } catch {
      // Sin permiso de portapapeles (o página sin https): se avisa para que se copie a mano.
      setCopiado("No se pudo copiar solo: seleccioná el texto y copialo.");
    }
  }

  return (
    <section className="keys-screen" aria-label="Access keys">
      <h1 className="keys-screen__titulo">Access keys</h1>
      <p className="keys-screen__ayuda">
        Enlaces privados para que reclutadores o empresas conversen con el asistente de{" "}
        <strong>{tenantNombre}</strong>. Cada key tiene vencimiento y, si querés, un cupo de preguntas.
      </p>

      {!requiereKeys && (
        <p className="keys-screen__aviso" role="note">
          Este tenant es de acceso público: las keys no se usan. Para activarlas, poné{" "}
          <code>"acceso": "access_key"</code> en su <code>tenant.json</code>.
        </p>
      )}

      <div className="keys-screen__fila">
        <label htmlFor={idClaveAdmin} className="keys-screen__label">
          API key de admin
        </label>
        <div className="keys-screen__con-boton">
          <input
            id={idClaveAdmin}
            type="password"
            autoComplete="off"
            value={apiKey}
            onChange={(evento) => onApiKeyChange(evento.target.value)}
          />
          <button type="button" onClick={cargar} disabled={!apiKey || cargando}>
            {cargando ? "Cargando..." : claves === null ? "Ver keys" : "Actualizar"}
          </button>
        </div>
      </div>

      {error && (
        <p className="keys-screen__error" role="alert">
          {error}
        </p>
      )}

      {requiereKeys && (
        <form className="keys-screen__form" onSubmit={crear}>
          <h2 className="keys-screen__subtitulo">Crear una key</h2>
          <label htmlFor={idEtiqueta} className="keys-screen__label">
            A quién se la das (reclutador o empresa)
          </label>
          <input
            id={idEtiqueta}
            type="text"
            maxLength={80}
            placeholder="Ana, Acme Recruiting"
            value={etiqueta}
            onChange={(evento) => setEtiqueta(evento.target.value)}
          />
          <div className="keys-screen__dos-columnas">
            <div>
              <label htmlFor={idDias} className="keys-screen__label">
                Vigencia (días)
              </label>
              <input
                id={idDias}
                type="number"
                min={1}
                max={365}
                value={dias}
                onChange={(evento) => setDias(evento.target.value)}
              />
            </div>
            <div>
              <label htmlFor={idCupo} className="keys-screen__label">
                Cupo de preguntas (opcional)
              </label>
              <input
                id={idCupo}
                type="number"
                min={1}
                placeholder="Sin límite"
                value={cupo}
                onChange={(evento) => setCupo(evento.target.value)}
              />
            </div>
          </div>
          <button type="submit" disabled={!apiKey || !etiqueta.trim() || creando}>
            {creando ? "Creando..." : "Crear key"}
          </button>
        </form>
      )}

      {creada && (
        <div className="keys-screen__creada" role="status">
          <p className="keys-screen__creada-titulo">
            Key creada para <strong>{creada.etiqueta}</strong>. Copiala ahora: no se vuelve a mostrar.
          </p>
          <label className="keys-screen__label" htmlFor={`${idEtiqueta}-enlace`}>
            {creada.enlace ? "Enlace para enviar" : "Key (el servidor no tiene configurada la URL del front)"}
          </label>
          <input
            id={`${idEtiqueta}-enlace`}
            className="keys-screen__enlace"
            type="text"
            readOnly
            value={creada.enlace ?? creada.key}
            onFocus={(evento) => evento.target.select()}
          />
          <div className="keys-screen__acciones">
            <button type="button" onClick={() => copiar(creada.enlace ?? creada.key, "Copiado.")}>
              {creada.enlace ? "Copiar enlace" : "Copiar key"}
            </button>
            <button
              type="button"
              className="keys-screen__secundario"
              onClick={() => {
                setCreada(null);
                setCopiado("");
              }}
            >
              Ya lo copié, cerrar
            </button>
            {copiado && <span className="keys-screen__copiado">{copiado}</span>}
          </div>
        </div>
      )}

      {claves !== null && (
        <div className="keys-screen__lista">
          <h2 className="keys-screen__subtitulo">Keys de {tenantNombre}</h2>
          {claves.length === 0 && <p className="keys-screen__vacio">Todavía no hay keys para este tenant.</p>}
          <ul className="keys-screen__items">
            {claves.map((clave) => (
              <li key={clave.key_id} className="keys-screen__item">
                <div className="keys-screen__item-cabecera">
                  <strong>{clave.etiqueta}</strong>
                  <span className={`keys-screen__estado keys-screen__estado--${clave.estado}`}>
                    {ETIQUETAS_ESTADO[clave.estado]}
                  </span>
                </div>
                <dl className="keys-screen__datos">
                  <div>
                    <dt>Vence</dt>
                    <dd>{fecha(clave.vence_en)}</dd>
                  </div>
                  <div>
                    <dt>Preguntas</dt>
                    <dd>{textoPreguntas(clave)}</dd>
                  </div>
                  <div>
                    <dt>Entradas</dt>
                    <dd>
                      {clave.accesos} · última {fecha(clave.ultimo_acceso_en)}
                    </dd>
                  </div>
                  <div>
                    <dt>Última pregunta</dt>
                    <dd>{fecha(clave.ultima_pregunta_en)}</dd>
                  </div>
                </dl>
                {clave.estado !== "revocada" &&
                  (confirmando === clave.key_id ? (
                    <div className="keys-screen__acciones">
                      <span>¿Revocar la key de {clave.etiqueta}? Corta el acceso al instante.</span>
                      <button
                        type="button"
                        className="keys-screen__peligro"
                        disabled={revocando === clave.key_id}
                        onClick={() => revocar(clave.key_id)}
                      >
                        {revocando === clave.key_id ? "Revocando..." : "Sí, revocar"}
                      </button>
                      <button type="button" className="keys-screen__secundario" onClick={() => setConfirmando("")}>
                        Cancelar
                      </button>
                    </div>
                  ) : (
                    <button
                      type="button"
                      className="keys-screen__secundario"
                      onClick={() => setConfirmando(clave.key_id)}
                    >
                      Revocar
                    </button>
                  ))}
              </li>
            ))}
          </ul>
        </div>
      )}
    </section>
  );
}

export default KeysScreen;
