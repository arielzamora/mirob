# ADR 0001 — Retrieval léxico (TF-IDF) como línea base antes de embeddings semánticos

- **Estado:** Aceptada
- **Contexto de etapa:** Etapa 1

## Contexto y problema

MiRob tiene que responder preguntas de reclutadores usando solo el contenido que cargo.
Antes de sumar un servicio de embeddings o un LLM necesito un mecanismo de recuperación
que pueda **entender paso a paso**, que sea **determinístico** (para testearlo con
asserts exactos) y que no dependa de la red ni de costos.

Además, quiero tener una **línea base medible**: si más adelante agrego embeddings
semánticos y búsqueda híbrida, tengo que poder demostrar con números si mejoran o no.

## Opciones consideradas

1. **TF-IDF + coseno** (`TfidfVectorizer` de scikit-learn para vectorizar; coseno calculado a mano con numpy).
2. **Embeddings semánticos** (por ejemplo, un deployment de embeddings en Azure AI Foundry).
3. **Librería de RAG "todo en uno"** (LangChain, LlamaIndex).

## Decisión

Se usa la **opción 1** en la Etapa 1.

- El coseno se implementa a mano (`app/rag/retrieve.py`): producto punto dividido por
  las normas, con protección contra división por cero. Un test lo compara contra
  `sklearn.metrics.pairwise.cosine_similarity` para confirmar que coincide.
- No se filtran *stopwords*: la lista integrada de scikit-learn es solo en inglés y el
  contenido está en español.
- Se descarta la opción 3: el objetivo del proyecto es entender cada paso, y esas
  librerías lo esconden.
- La opción 2 **no se descarta**: se suma en la Etapa 2 **sin reemplazar** al TF-IDF,
  para poder comparar léxico vs. semántico vs. híbrido.

## Consecuencias

**Positivas**

- Determinístico: mismas entradas, mismo ranking. Los tests afirman el top-1 exacto de
  preguntas conocidas sobre el perfil ficticio.
- Sin red, sin costo, sin claves; corre en cualquier máquina.
- Sirve de línea base para las evals de la Etapa 2 (Recall@k y MRR por modo).

**Negativas / límites conocidos**

- Es **léxico, no semántico**: sin palabras en común no hay coincidencia, y no entiende
  sinónimos ni paráfrasis.
- Las palabras de la pregunta que también aparecen en otros chunks pueden desviar el
  resultado. Ejemplo real durante el armado del perfil ficticio: la pregunta
  *"¿Dónde trabaja actualmente?"* traía el chunk de residencia (score ≈ 0,51) porque
  compartía "dónde" y "actualmente" con él; se corrigió ajustando la redacción del chunk,
  no el algoritmo.
- Una pregunta fuera de tema igual devuelve **un** chunk como top-1, pero con score bajo
  (la pregunta *"¿Cuál es la capital de Mongolia?"* da ≈ 0,23 sobre el perfil ficticio).
  El score es la única señal para decir "no tengo esa información"; hoy el umbral
  (0,25) solo lo usa el modo `humanized`.

## Seguimiento

- Etapa 2: embeddings semánticos y búsqueda híbrida, medidos contra esta línea base con
  un golden set (ver el roadmap del README).
