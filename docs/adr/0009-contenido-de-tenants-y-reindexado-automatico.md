# ADR 0009 — Contenido de los tenants: formato, almacenamiento y reindexado automático

- **Estado:** Aceptada
- **Contexto de etapa:** Etapa 3, Parte 3.1

## Contexto y problema

El contenido es lo que el bot "sabe": hay que poder **actualizarlo sin redeployar**, mantenerlo **fuera de git y de la
imagen** (datos personales) y que el retrieval, que es léxico (ADR 0001), lo encuentre bien.

## Decisión

**Formato.** Archivos `.md` o `.txt` por tenant, con un tema por `## Encabezado` y pares `P:` / `R:`. Cada chunk es una
sección. Como el retrieval es léxico, **cada `P:` lleva varias formulaciones** de la pregunta ("¿Cómo los contacto?
¿Tienen WhatsApp? ¿Cuál es el email?") y se redacta con las palabras que usaría el visitante: sin palabras en común no hay
coincidencia (por ejemplo "contactar" frente a "contacto").

**Reglas de redacción.** Solo se escribe lo que la fuente dice: no se inventan datos ni precios, y lo que no está se deja
fuera para que el bot diga que no lo sabe. Las afirmaciones de marketing (cantidad de clientes, años) se copian
tal cual y se marcan para revisión.

**Dónde vive.** En el share `mirob-data` de Azure Files, una carpeta por tenant (`digital-zur/`, `ariel-zamora/`),
montada en `/app/data/tenants`. En mi máquina, `backend/data/tenants/<tenant>/` (ignorado por git). Se sube con
`az storage file upload-batch`.

**Reindexado automático.** `get_state()` guarda junto al índice una **huella** de los archivos (nombre, fecha de
modificación y tamaño) y cada **30 segundos** la compara: si cambió, reconstruye el índice de ese tenant. Un tenant
**sin contenido** se revisa en cada consulta. `POST /admin/reindex` (con la API key) fuerza el reindexado al instante.

**Calibración.** Cada tenant define su `umbral_similitud`. Se calibra con un conjunto de preguntas típicas y fuera de tema
(para `digital-zur`: 27 preguntas, todas con el chunk esperado, y las fuera de tema con score 0).

## Opciones descartadas

- Blob Storage con sincronización (SDK y código extra); contenido dentro de la imagen (datos personales en un artefacto).
- Reindexar en cada consulta: leer el share cuesta tiempo y dinero; 30 segundos alcanzan para un cambio manual.
- Detectar cambios solo reiniciando el backend: obliga a tocar la infraestructura para cambiar un texto.

## Consecuencias

**Positivas**

- Editar un texto es subir un archivo; se refleja en menos de un minuto, sin deploy ni clave.
- El contenido real no pasa por git, por la imagen ni por el pipeline.

**Negativas / límites conocidos**

- **El contenido no está versionado.** La copia local y el share son las únicas copias: hay que respaldarla.
- Hay hasta 30 segundos de desfase entre subir un archivo y que se vea.
- El índice se reconstruye por completo en cada cambio (aceptable para unos pocos KB).
- Un retrieval léxico sigue sin entender sinónimos; se compensa con contenido bien redactado (o con embeddings, Parte 2.1).
- El contenido de `ariel-zamora` no se carga hasta que existan las access keys (Parte 3.0): la API de chat es pública.

## Seguimiento

- Respaldo del contenido (por ejemplo, un repositorio privado aparte o copias del share).
- Embeddings semánticos para reducir la dependencia de la redacción de las preguntas.
