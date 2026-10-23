import { useId, useState } from "react";
import "./AdminScreen.css";
import { postAdminContent } from "./api.js";

// Una sola caja de carga simple (nada de las 3 secciones separadas todavía,
// eso es Etapa 2). Sube al tenant elegido en el selector del header. La API key se pide acá y vive SOLO en el estado de React:
// no se guarda en localStorage ni en la URL, así se pierde al recargar la página.
function AdminScreen({ tenantId, tenantNombre }) {
  const [archivo, setArchivo] = useState(null);
  const [apiKey, setApiKey] = useState("");
  const [subiendo, setSubiendo] = useState(false);
  const [resultado, setResultado] = useState(null); // { tipo: "ok" | "error", mensaje }
  const inputId = useId();
  const apiKeyId = useId();

  async function handleSubmit(evento) {
    evento.preventDefault();
    if (!archivo || !apiKey || subiendo) return;

    setSubiendo(true);
    setResultado(null);

    try {
      const respuesta = await postAdminContent(archivo, apiKey, tenantId);
      setResultado({
        tipo: "ok",
        mensaje: `"${respuesta.filename}" cargado en ${tenantNombre}. Corpus reindexado: ${respuesta.total_chunks} chunks de ${respuesta.total_files} archivo(s).`,
      });
      setArchivo(null);
      evento.target.reset();
    } catch (error) {
      setResultado({ tipo: "error", mensaje: error.message });
    } finally {
      setSubiendo(false);
    }
  }

  return (
    <section className="admin-screen" aria-label="Carga de contenido">
      <h1 className="admin-screen__titulo">Cargar contenido</h1>
      <p className="admin-screen__ayuda">
        Subí un archivo .md o .txt para agregarlo al contenido de{" "}
        <strong>{tenantNombre}</strong>. Al subirlo se reindexa solo el contenido de este
        tenant; los demás no se tocan.
      </p>

      <form className="admin-screen__form" onSubmit={handleSubmit}>
        <label htmlFor={apiKeyId} className="admin-screen__label">
          API key de admin
        </label>
        <input
          id={apiKeyId}
          type="password"
          autoComplete="off"
          value={apiKey}
          onChange={(evento) => setApiKey(evento.target.value)}
          disabled={subiendo}
        />

        <label htmlFor={inputId} className="admin-screen__label">
          Archivo (.md o .txt)
        </label>
        <input
          id={inputId}
          type="file"
          accept=".md,.txt"
          onChange={(evento) => setArchivo(evento.target.files[0] ?? null)}
          disabled={subiendo}
        />

        <button type="submit" disabled={!archivo || !apiKey || subiendo}>
          {subiendo ? "Subiendo..." : "Subir"}
        </button>
      </form>

      {resultado && (
        <p
          className={`admin-screen__resultado admin-screen__resultado--${resultado.tipo}`}
          role="status"
        >
          {resultado.mensaje}
        </p>
      )}
    </section>
  );
}

export default AdminScreen;
