import { useEffect, useRef, useState } from "react";
import "./ChatScreen.css";
import { postChat } from "./api.js";
import ChatMeta from "./ChatMeta.jsx";
import ModeSwitch from "./ModeSwitch.jsx";
import RobotEspera from "./RobotEspera.jsx";

let nextMessageId = 1;

// Pasado este tiempo esperando respuesta, se avisa que el servidor estaba "dormido" para
// que la persona no crea que el chat se colgó (el primer pedido tras un rato sin uso tarda).
const MS_ANTES_DE_AVISO_LARGO = 5000;

// Límites del contrato de POST /api/v1/chat (los valida el backend; acá se respetan
// para no mandar requests que el servidor rechazaría con 422).
const MAX_CARACTERES_MENSAJE = 350;
const MAX_CARACTERES_HISTORIAL = 1000;
// El LLM solo usa los últimos 6 mensajes del historial: mandar más sería gastar ancho de banda.
const MAX_MENSAJES_HISTORIAL = 6;

// Convierte los mensajes de la pantalla al formato `conversation_history` de la API.
function armarHistorial(mensajes) {
  return mensajes
    .filter((mensaje) => !mensaje.esError)
    .slice(-MAX_MENSAJES_HISTORIAL)
    .map((mensaje) => ({
      role: mensaje.rol === "usuario" ? "user" : "assistant",
      content: mensaje.texto.slice(0, MAX_CARACTERES_HISTORIAL),
    }));
}

// `tenantId` define con qué cliente se conversa. El padre usa `key={tenantId}`, así que
// al cambiar de tenant este componente se remonta y el historial arranca vacío:
// nunca se mezclan conversaciones de distintos tenants.
// `embed` = widget/enlace público: sin selector de modo ni detalle de auditoría.
// `conectando` = la API todavía no respondió al primer pedido de la página (puede estar
// arrancando); se muestra el robot en lugar del saludo, pero igual se puede escribir.
function ChatScreen({ tenantId, tenantNombre, embed, modoInicial, conectando = false }) {
  // Historial en estado local (ver CLAUDE.md: nada de persistencia todavía).
  // Se pierde al recargar la página — es intencional en esta etapa.
  const [mensajes, setMensajes] = useState([]);
  const [pregunta, setPregunta] = useState("");
  const [modo, setModo] = useState(modoInicial);
  const [enviando, setEnviando] = useState(false);
  const [esperaLarga, setEsperaLarga] = useState(false);

  const finDelHistorialRef = useRef(null);

  // Mientras se espera la respuesta, un temporizador activa el aviso largo. El `return` del
  // efecto limpia el temporizador al terminar (como un CancellationToken / Dispose en .NET).
  useEffect(() => {
    if (!enviando) return undefined;
    const temporizador = setTimeout(() => setEsperaLarga(true), MS_ANTES_DE_AVISO_LARGO);
    return () => clearTimeout(temporizador);
  }, [enviando]);

  useEffect(() => {
    finDelHistorialRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [mensajes]);

  async function handleSubmit(evento) {
    evento.preventDefault();

    const preguntaLimpia = pregunta.trim();
    if (!preguntaLimpia || enviando) return;

    // El historial se arma ANTES de agregar la pregunta actual: va aparte, en `message`.
    const conversationHistory = armarHistorial(mensajes);

    const mensajeUsuario = {
      id: nextMessageId++,
      rol: "usuario",
      texto: preguntaLimpia,
    };
    setMensajes((previos) => [...previos, mensajeUsuario]);
    setPregunta("");
    setEnviando(true);

    try {
      const resultado = await postChat({
        tenantId,
        message: preguntaLimpia,
        mode: modo,
        conversationHistory,
      });
      setMensajes((previos) => [
        ...previos,
        {
          id: nextMessageId++,
          rol: "bot",
          texto: resultado.answer,
          meta: {
            mode: resultado.mode,
            topScore: resultado.top_score,
            threshold: resultado.threshold,
            belowThreshold: resultado.below_threshold,
            llmCalled: resultado.llm_called,
            usage: resultado.usage,
            latencyMs: resultado.latency_ms,
            deployment: resultado.deployment,
            chunks: resultado.chunks,
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
      setEsperaLarga(false); // el próximo envío vuelve a empezar con el mensaje corto
    }
  }

  return (
    <section className="chat-screen" aria-label={`Chat con ${tenantNombre}`}>
      {!embed && (
        <div className="chat-screen__barra">
          <ModeSwitch value={modo} onChange={setModo} disabled={enviando} />
        </div>
      )}

      <div className="chat-screen__historial" role="log" aria-live="polite">
        {mensajes.length === 0 && conectando && (
          <div className="chat-screen__vacio">
            <RobotEspera texto="Conectando con el asistente" />
          </div>
        )}

        {mensajes.length === 0 && !conectando && (
          <p className="chat-screen__vacio">
            Estás conversando con el asistente de <strong>{tenantNombre}</strong>. Escribí tu
            pregunta para empezar.
          </p>
        )}

        {mensajes.map((mensaje) => (
          <div
            key={mensaje.id}
            className={`burbuja burbuja--${mensaje.rol} ${mensaje.esError ? "burbuja--error" : ""}`}
          >
            <p className="burbuja__texto">{mensaje.texto}</p>
            {mensaje.meta && !embed && <ChatMeta meta={mensaje.meta} />}
          </div>
        ))}

        {enviando && (
          <div className="burbuja burbuja--bot burbuja--cargando">
            <RobotEspera
              pequeno
              texto={esperaLarga ? "Estoy despertando, un momento más" : "Pensando"}
            />
          </div>
        )}

        <div ref={finDelHistorialRef} />
      </div>

      <form className="chat-screen__form" onSubmit={handleSubmit}>
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
            maxLength={MAX_CARACTERES_MENSAJE}
          />
        </label>
        <span
          className="chat-screen__contador"
          aria-label={`${pregunta.length} de ${MAX_CARACTERES_MENSAJE} caracteres`}
        >
          {pregunta.length}/{MAX_CARACTERES_MENSAJE}
        </span>

        <button type="submit" disabled={enviando || !pregunta.trim()}>
          Enviar
        </button>
      </form>
    </section>
  );
}

export default ChatScreen;
