import "./ChatMeta.css";
import { MOTIVOS_DEGRADACION } from "./modos.js";

const NOMBRES_RETRIEVAL = { lexico: "léxico", semantico: "semántico", hibrido: "híbrido" };

// Detalle de auditoría de una respuesta del bot: scores, umbral, costo y chunks
// recuperados. Solo se muestra en la vista de pruebas (no en el widget embebido).
function ChatMeta({ meta }) {
  const {
    mode,
    topScore,
    threshold,
    belowThreshold,
    llmCalled,
    usage,
    latencyMs,
    deployment,
    chunks,
    retrievalUsed,
    retrievalFallbackReason,
    embeddingUsage,
  } = meta;

  // Tokens de embeddings (consulta y, la primera vez, los chunks) aparte de los del LLM: así se compara el costo
  // de cada retrieval con la misma pregunta. El léxico siempre da 0.
  const tokensEmbeddings = embeddingUsage?.total_tokens ?? 0;
  const tokensLlm = llmCalled ? usage.total_tokens : 0;
  const tokensTotal = tokensEmbeddings + tokensLlm;

  return (
    <div className="chat-meta">
      <p className="chat-meta__resumen">
        <span className="chat-meta__etiqueta">{mode}</span>
        <span className="chat-meta__etiqueta">{NOMBRES_RETRIEVAL[retrievalUsed] ?? retrievalUsed}</span>
        <span>
          score {topScore.toFixed(3)} / umbral {threshold.toFixed(2)}
        </span>
        {belowThreshold && <span className="chat-meta__aviso">bajo el umbral</span>}
        {retrievalFallbackReason && (
          <span className="chat-meta__aviso">
            se usó léxico: {MOTIVOS_DEGRADACION[retrievalFallbackReason] ?? retrievalFallbackReason}
          </span>
        )}
      </p>

      <p className="chat-meta__resumen">
        <span>Embeddings: {tokensEmbeddings} tokens</span>
        {mode === "foundry" && (
          <span>
            {llmCalled
              ? `LLM: ${usage.total_tokens} tokens (${usage.input_tokens} in / ${usage.output_tokens} out) · ${Math.round(latencyMs)} ms · ${deployment}`
              : "LLM no invocado · 0 tokens"}
          </span>
        )}
        <span>
          <strong>Total: {tokensTotal} tokens</strong>
        </span>
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
