import { useEffect, useRef, useState } from "react";
import "./ChatScreen.css";
import { postChat } from "./api.js";

let nextMessageId = 1;

function ChatScreen() {
  // Historial en estado local (ver CLAUDE.md: nada de persistencia todavía).
  // Se pierde al recargar la página — es intencional en esta etapa.
  const [mensajes, setMensajes] = useState([]);
  const [pregunta, setPregunta] = useState("");
  const [modo, setModo] = useState("raw");
  const [enviando, setEnviando] = useState(false);

  const finDelHistorialRef = useRef(null);

  useEffect(() => {
    finDelHistorialRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [mensajes]);

  async function handleSubmit(evento) {
    evento.preventDefault();

    const preguntaLimpia = pregunta.trim();
    if (!preguntaLimpia || enviando) return;

    const mensajeUsuario = {
      id: nextMessageId++,
      rol: "usuario",
      texto: preguntaLimpia,
    };
    setMensajes((previos) => [...previos, mensajeUsuario]);
    setPregunta("");
    setEnviando(true);

    try {
      const resultado = await postChat(preguntaLimpia, modo);
      setMensajes((previos) => [
        ...previos,
        {
          id: nextMessageId++,
          rol: "bot",
          texto: resultado.respuesta,
          meta: {
            source: resultado.source,
            seccion: resultado.seccion,
            score: resultado.score,
            modo: resultado.modo,
          },
        },
      ]);
    } catch (error) {
      setMensajes((previos) => [
        ...previos,
        { id: nextMessageId++, rol: "bot", texto: error.message, esError: true },
      ]);
    } finally {
      setEnviando(false);
    }
  }

  return (
    <section className="chat-screen" aria-label="Chat con MiRob">
      <div className="chat-screen__historial" role="log" aria-live="polite">
        {mensajes.length === 0 && (
          <p className="chat-screen__vacio">
            Preguntale algo a MiRob sobre el perfil profesional, personal o las
            habilidades blandas de Ariel.
          </p>
        )}

        {mensajes.map((mensaje) => (
          <div
            key={mensaje.id}
            className={`burbuja burbuja--${mensaje.rol} ${mensaje.esError ? "burbuja--error" : ""}`}
          >
            <p className="burbuja__texto">{mensaje.texto}</p>
            {mensaje.meta && (
              <p className="burbuja__meta">
                {mensaje.meta.source} · {mensaje.meta.seccion} · score{" "}
                {mensaje.meta.score.toFixed(3)} · modo {mensaje.meta.modo}
              </p>
            )}
          </div>
        ))}

        {enviando && (
          <div className="burbuja burbuja--bot burbuja--cargando" aria-hidden="true">
            <p className="burbuja__texto">...</p>
          </div>
        )}

        <div ref={finDelHistorialRef} />
      </div>

      <form className="chat-screen__form" onSubmit={handleSubmit}>
        <label className="chat-screen__campo" htmlFor="modo-select">
          <span className="chat-screen__label-visually-hidden">Modo de respuesta</span>
          <select
            id="modo-select"
            value={modo}
            onChange={(evento) => setModo(evento.target.value)}
            disabled={enviando}
          >
            <option value="raw">Raw</option>
            <option value="humanized">Humanizado</option>
          </select>
        </label>

        <label className="chat-screen__campo chat-screen__campo--input" htmlFor="pregunta-input">
          <span className="chat-screen__label-visually-hidden">Pregunta</span>
          <input
            id="pregunta-input"
            type="text"
            placeholder="Escribí tu pregunta..."
            value={pregunta}
            onChange={(evento) => setPregunta(evento.target.value)}
            disabled={enviando}
            autoComplete="off"
          />
        </label>

        <button type="submit" disabled={enviando || !pregunta.trim()}>
          Enviar
        </button>
      </form>
    </section>
  );
}

export default ChatScreen;
