// Los tres modos que acepta POST /api/v1/chat. El orden va de "sin procesar" a
// "redactado por el LLM", para comparar de un vistazo.
export const MODOS = [
  { id: "raw", label: "Raw", ayuda: "El chunk recuperado, sin procesar (audita la similitud)" },
  { id: "humanized", label: "Humanizado", ayuda: "El chunk con una apertura conversacional (sin LLM, 0 tokens)" },
  { id: "foundry", label: "Foundry", ayuda: "Respuesta redactada por el LLM (solo si supera el umbral)" },
];
