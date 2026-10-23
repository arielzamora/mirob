import "./ChatMeta.css";

// Detalle de auditoría de una respuesta del bot: scores, umbral, costo y chunks
// recuperados. Solo se muestra en la vista de pruebas (no en el widget embebido).
function ChatMeta({ meta }) {
  const { mode, topScore, threshold, belowThreshold, llmCalled, usage, latencyMs, deployment, chunks } = meta;

  return (
    <div className="chat-meta">
      <p className="chat-meta__resumen">
        <span className="chat-meta__etiqueta">{mode}</span>
        <span>
          score {topScore.toFixed(3)} / umbral {threshold.toFixed(2)}
        </span>
        {belowThreshold && <span className="chat-meta__aviso">bajo el umbral</span>}
        {mode === "foundry" && (
          <span>
            {llmCalled
              ? `LLM: ${usage.total_tokens} tokens (${usage.input_tokens} in / ${usage.output_tokens} out) · ${Math.round(latencyMs)} ms · ${deployment}`
              : "LLM no invocado · 0 tokens"}
          </span>
        )}
      </p>

      <details className="chat-meta__chunks">
        <summary>Chunks recuperados ({chunks.length})</summary>
        <ol>
          {chunks.map((chunk) => (
            <li key={chunk.chunk_id}>
              <p className="chat-meta__chunk-titulo">
                {chunk.source} · {chunk.section} · score {chunk.score.toFixed(3)}
                {chunk.score >= threshold ? " · supera el umbral" : " · bajo el umbral"}
              </p>
              <pre className="chat-meta__chunk-texto">{chunk.text}</pre>
            </li>
          ))}
        </ol>
      </details>
    </div>
  );
}

export default ChatMeta;
