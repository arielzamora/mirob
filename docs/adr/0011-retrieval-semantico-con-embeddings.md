# ADR 0011 — Retrieval semántico e híbrido con embeddings, sin Azure AI Search

- **Estado:** Aceptada (implementada y probada con un generador falso; la calibración y la prueba contra el modelo real quedan a cargo de cada tenant, ver "Seguimiento")
- **Contexto de etapa:** Etapa 2, Parte 2.1

## Contexto y problema

El retrieval de MiRob es léxico (TF-IDF, [ADR 0001](0001-retrieval-lexico-tfidf.md)): solo encuentra un chunk si la pregunta
comparte palabras con él. Eso obliga a redactar cada `P:` con muchas variantes ([ADR 0009](0009-contenido-de-tenants-y-reindexado-automatico.md))
y falla con sinónimos y paráfrasis: "¿Me pasás su **celular**?" no encontraba el chunk que habla de "**teléfono**". El bot
queda más frágil de lo que el contenido merece, justo donde más importa (reclutadores y clientes de Digital Zur).

Hay que sumar búsqueda por **significado**, sin perder lo que el léxico hace bien (una sigla como "AZ-104", un nombre
propio), sin cambiar el principio de "nunca inventar" y sin romper el control de costos ([ADR 0004](0004-gobierno-de-costos-de-ia.md))
ni el aislamiento entre tenants ([ADR 0003](0003-multitenancy-y-aislamiento.md)).

## Opciones consideradas

1. **Embeddings en el propio backend:** un deployment de embeddings en Foundry, vectores en memoria y similitud coseno con
   numpy, persistidos por tenant en Azure Files.
2. **Azure AI Search** (búsqueda vectorial e híbrida administrada).
3. **Otra base con vectores** (PostgreSQL con pgvector, Cosmos DB, etc.).
4. **Reemplazar TF-IDF por embeddings.**
5. Un framework de RAG "todo en uno" (LangChain, LlamaIndex): descartado desde el [ADR 0001](0001-retrieval-lexico-tfidf.md).

## Decisión

Se usa la **opción 1** y el léxico **no se reemplaza** (se descarta la 4): queda como modo por defecto y como respaldo.

**Tres retrievals, independientes del modo de respuesta.** El campo `retrieval` de `POST /api/v1/chat` es `lexico`, `semantico`
o `hibrido`, y es independiente de `mode` (`raw`, `humanized`, `foundry`, que es cómo se *redacta* la respuesta). Si el pedido
no lo trae, rige `retrieval.por_defecto` del `tenant.json` (el léxico si no se define). El widget público nunca lo manda: lo decide
el servidor por tenant. La vista de auditoría del front tiene un segundo selector para comparar los tres con la misma pregunta.

**Embeddings.** Deployment `text-embedding-3-small` en el mismo proyecto de Foundry que el de chat, nombre en
`FOUNDRY_EMBEDDINGS_DEPLOYMENT`. Mismo `DefaultAzureCredential`, mismo `AIProjectClient` y mismo token que el chat ([ADR 0008](0008-foundry-autenticacion-modelo-y-prompts.md)):
**sin API keys**. **Pero otra ruta:** el chat usa la del proyecto (`…/api/projects/<proyecto>/openai/v1`), que **responde 404 sin cuerpo a
`/embeddings`** (comprobado con `labs/lab02b_embeddings_diagnostico.py`); los embeddings se piden por la del **recurso**
(`https://<recurso>.services.ai.azure.com/openai/v1`), con `get_openai_client(base_url=…)` y el mismo alcance
(`https://ai.azure.com/.default`). El único código que importa el SDK es `app/foundry/embeddings.py` (y `client.py`); el dominio depende de un `Protocol`
(`GeneradorEmbeddings`), así que se prueba sin red con un generador falso. Los textos viajan **en lotes** (32 por defecto,
`MIROB_EMBEDDINGS_BATCH`), no uno por chunk, y los vectores se **normalizan una sola vez**, con lo que el coseno es un
producto punto (`app/rag/semantico.py`).

**Persistencia e indexado incremental** (`app/rag/almacen_vectores.py`). Cada tenant guarda sus vectores en su propia carpeta
de Azure Files: `<tenant>/.vectores/manifest.json` y `vectores-<id>.npy`. Nunca hay un archivo compartido: aunque dos tenants
tengan un chunk idéntico, cada uno calcula y guarda el suyo. Cada chunk se identifica por el sha256 de su texto; cuando
cambia el contenido ([ADR 0009](0009-contenido-de-tenants-y-reindexado-automatico.md)) solo se vectorizan los chunks nuevos o modificados y se descartan los que
ya no existen. Si cambia el deployment o las dimensiones, se recalcula todo. La escritura es atómica (temporal + `os.replace`,
y el manifest se reemplaza al final), un archivo dañado se ignora y se recalcula sin error, y `.npy` nunca se carga con pickle.
La carpeta `.vectores` no cuenta como contenido: no altera la huella del reindexado. El tenant `demo` (solo lectura) no guarda nada.

**Híbrido** (`app/rag/hibrido.py`). El coseno de TF-IDF y el de embeddings no están en la misma escala (el de embeddings casi
nunca baja de ~0,2 aunque no haya relación). Antes de combinarlos, cada puntaje se lleva a [0, 1] con un **piso y un techo fijos
del tenant** (el piso es el mejor puntaje que sacó una pregunta fuera de tema; el techo, el puntaje típico del chunk correcto) y
se combinan con un peso: `peso · semántico + (1 − peso) · léxico`. Se usan límites fijos y no el mínimo y máximo de cada
consulta, porque con estos últimos el mejor chunk de una pregunta fuera de tema también llegaría a 1 y ningún umbral podría decir
"no tengo información".

**Umbrales por modo** (ADR 0004). Cada modo tiene su propio umbral en `tenant.json`, calibrado con preguntas típicas y fuera de
tema, como se hizo con el léxico:

```json
"retrieval": {
  "por_defecto": "hibrido",
  "semantico": { "umbral": 0.45 },
  "hibrido": { "umbral": 0.5, "peso_semantico": 0.7, "piso_lexico": 0.0, "techo_lexico": 0.6,
               "piso_semantico": 0.25, "techo_semantico": 0.7 }
}
```

`umbral_similitud` sigue siendo el del léxico. Un modo sin bloque **no está calibrado y no se ofrece**; `por_defecto` solo puede
apuntar a un modo calibrado (si no, el tenant no carga). La calibración la hace `python -m scripts.calibrar_retrieval --tenant <id>`
a partir de un `calibracion.json` por tenant (preguntas en tema, cada una con el encabezado del chunk esperado, y preguntas fuera
de tema): mide el top-1 de cada modo, sugiere el umbral que mejor separa lo que debe responder de lo que no, prueba los pesos y
imprime el bloque para pegar. No modifica ningún archivo.

**Costos** (ADR 0004). Los tokens de embeddings, tanto de armar los vectores como de cada pregunta, **cuentan en el tope diario**
del tenant y global (`GeneradorEmbeddingsConPresupuesto`) y se informan en la respuesta (`embedding_usage`), aparte de los del
LLM. A diferencia del léxico (0 tokens), cada pregunta semántica o híbrida gasta una llamada chica a embeddings **aunque quede
bajo el umbral**; la acota el límite de pedidos por IP del chat y el tope diario.

**Degradación, nunca error.** Si el modo no está calibrado, falta el deployment, Azure falla, cambian las dimensiones o se
agotó el tope diario, el chat responde con el retrieval léxico y lo declara (`retrieval_used` y `retrieval_fallback_reason`:
`modo_no_calibrado`, `embeddings_no_configurados`, `embeddings_no_disponibles`, `presupuesto_agotado`). Un problema con los
embeddings nunca deja al bot mudo; y el umbral que se aplica es el del retrieval realmente usado.

**Infraestructura.** El deployment de embeddings se crea **a mano** en Foundry, igual que el de chat. El Bicep lo **referencia**
por nombre (`embeddingsDeploymentName`) y solo agrega la variable de entorno; no crea ningún recurso (ver `infra/README.md`).

## Por qué sin Azure AI Search, a esta escala

- **El tamaño lo permite de sobra.** Cada tenant tiene decenas de chunks (22 el demo, ~40 digital-zur, ~66 ariel-zamora). Con
  1536 dimensiones por defecto, la matriz de un tenant pesa unos cientos de KB; comparar una pregunta contra todos los chunks es
  una multiplicación de matriz por vector de microsegundos. Un servicio de búsqueda aporta indexación y escala que acá no se
  usan.
- **Costo y complejidad.** Los tiers pagos de AI Search cobran por unidad de búsqueda y por hora (a confirmar en la calculadora
  de Azure), y MiRob se rige por "nada que cobre por hora". Sumaría otro recurso con su identidad y sus permisos, una canalización
  de ingesta para mantener el índice sincronizado con los `.md`, y una llamada de red más en cada pregunta.
- **Ya hay dónde persistir.** El contenido vive en Azure Files por tenant (ADR 0009) y las access keys en un JSON del mismo
  share (ADR 0010): los vectores usan la misma pieza y el mismo aislamiento por carpeta.
- **Se puede entender paso a paso**, que es parte del propósito del proyecto (ADR 0001): todo el retrieval son unas decenas
  de líneas de numpy que se prueban con asserts exactos.

**Cuándo convendría Azure AI Search:** cuando el contenido deje de caber cómodo en memoria por réplica (del orden de decenas de
miles de chunks por tenant o muchos tenants grandes); cuando haga falta más de una réplica con **un índice compartido**
(hoy cada réplica tendría los suyos); cuando se necesiten filtros, facetas, reranking semántico administrado o búsqueda
sobre documentos largos (PDF, páginas) con troceado automático; o cuando el contenido de un tenant deba tener permisos por
documento. En ese caso, el `Protocol` de embeddings y `buscar.py` son el punto de reemplazo.

## Consecuencias

**Positivas**

- Entiende sinónimos y paráfrasis sin tener que reescribir el contenido con más variantes.
- El léxico queda intacto como default y como respaldo: ningún tenant cambia de comportamiento hasta calibrar.
- Reiniciar el backend (escala a cero) no vuelve a pagar los embeddings del contenido; un cambio de contenido paga solo lo
  modificado.
- Todo es testeable sin red: generador falso, carpetas temporales, relojes y atomicidad con fallos simulados.

**Negativas / límites conocidos**

- **El texto de cada pregunta viaja al endpoint de embeddings** (el mismo recurso de Foundry que el chat). MiRob no lo guarda ni lo
  loguea (solo se registran tipos de error y números), pero es un pedido más a Azure por pregunta.
- **La calibración es tan buena como sus preguntas.** Con 27 a 41 preguntas por tenant, los umbrales y el peso del híbrido son
  frágiles; conviene que sean paráfrasis reales de lo que preguntan los visitantes, no copias de las líneas `P:` del contenido.
  Falta un golden set con Recall@k y MRR por modo (Parte 2.3) para medirlo de forma sostenida.
- **Cambiar de modelo invalida los vectores y la calibración.** El backend detecta el cambio de deployment o de dimensiones y
  recalcula, pero una versión nueva del modelo bajo el mismo nombre de deployment **no se detecta** (solo si cambian las dimensiones);
  hay que revisar el calendario de retiros del modelo (como el de chat, ADR 0008) y recalibrar.
- **No arregla el contenido ausente:** si la respuesta no está en el contenido, ningún retrieval la inventa; el umbral y el prompt
  siguen siendo lo que hace decir "no tengo esa información".
- **Vectores por réplica en memoria** y el almacenamiento asume una sola réplica, como los demás contadores (ADR 0003 y 0004).
- La **primera consulta semántica tras un reinicio** lee los vectores del share; si no estaban guardados (tenant nuevo, otro
  modelo), los calcula en ese momento y tarda más una sola vez.
- No resuelve la inyección de instrucciones por contenido cargado: los embeddings solo cambian cómo se *busca*, no qué se le
  entrega al LLM (ver la Parte 2.5 del plan).

## Seguimiento

- Calibrar `digital-zur` y `ariel-zamora` con `python -m scripts.calibrar_retrieval` y pegar el bloque `retrieval` en su
  `tenant.json`; recién entonces activar `por_defecto`.
- Probar el deployment contra Foundry con `python -m labs.lab02_embeddings_smoke` (hecho en local con la cuenta de quien desarrolla).
- **Permisos de la identidad del backend en producción.** El rol *Foundry User* de la Container App está asignado **solo sobre el
  proyecto** (ADR 0008), pero los embeddings se llaman por la ruta del **recurso**. En local funciona porque la cuenta de quien
  desarrolla tiene permisos más amplios; en producción **no está verificado** que el rol sobre el proyecto alcance. Si falla, el chat
  no se rompe (responde con léxico y `retrieval_fallback_reason: "embeddings_no_disponibles"`) y habrá que asignar el rol necesario
  sobre el recurso, en el Bicep.
- Golden set con Recall@k y MRR por modo, incluidos los casos de sinónimos (Parte 2.3).
- Precalentar los vectores al arrancar para los tenants que usen un modo con embeddings por defecto (hoy los arma la primera consulta).
- Fijar `versionUpgradeOption` del deployment de embeddings, como el de chat (ADR 0008).
