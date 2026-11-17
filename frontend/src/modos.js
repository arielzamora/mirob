// Los tres modos que acepta POST /api/v1/chat. El orden va de "sin procesar" a
// "redactado por el LLM", para comparar de un vistazo.
export const MODOS = [
  { id: "raw", label: "Raw", ayuda: "El chunk recuperado, sin procesar (audita la similitud)" },
  { id: "humanized", label: "Humanizado", ayuda: "El chunk con una apertura conversacional (sin LLM, 0 tokens)" },
  { id: "foundry", label: "Foundry", ayuda: "Respuesta redactada por el LLM (solo si supera el umbral)" },
];

// Cómo se BUSCAN los chunks (campo `retrieval` del chat), independiente del modo de arriba, que es cómo se redacta
// la respuesta. Combinando los dos se compara con la misma pregunta qué trae cada búsqueda y cuánto cuesta.
// El id "" no manda el campo: el servidor usa el retrieval por defecto del tenant (lo mismo que ve el widget público).
export const RETRIEVALS = [
  { id: "", label: "Del tenant", ayuda: "No manda el campo: rige el retrieval por defecto del tenant (como en el widget público)" },
  { id: "lexico", label: "Léxico", ayuda: "TF-IDF: palabras en común. 0 tokens de embeddings" },
  { id: "semantico", label: "Semántico", ayuda: "Embeddings: significado, aunque no compartan palabras" },
  { id: "hibrido", label: "Híbrido", ayuda: "Combina léxico y semántico con el peso calibrado del tenant" },
];

// Por qué una búsqueda con embeddings terminó resuelta con el léxico (campo `retrieval_fallback_reason`).
export const MOTIVOS_DEGRADACION = {
  modo_no_calibrado: "ese modo no está calibrado en este tenant",
  embeddings_no_configurados: "no hay deployment de embeddings configurado",
  embeddings_no_disponibles: "Azure no respondió (error o dimensiones distintas)",
  presupuesto_agotado: "se alcanzó el tope diario de tokens",
};
