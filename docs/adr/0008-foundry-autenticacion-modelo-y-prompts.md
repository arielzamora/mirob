# ADR 0008 — Foundry: autenticación, modelo y comportamiento del asistente

- **Estado:** Aceptada (verificada con el modelo real y en producción)
- **Contexto de etapa:** Etapa 2, Partes 2.0 y 2.2

## Contexto y problema

MiRob habla en nombre de una persona o de una empresa: **no puede inventar**. Hay que elegir cómo se
autentica contra Foundry, qué modelo usa y cómo se le instruye, cuidando el costo (ver ADR 0004).

## Opciones consideradas

**Autenticación:** API key del recurso, o **Microsoft Entra ID** (`DefaultAzureCredential`).

**Modelo:** `gpt-4o-mini` (el plan original), `gpt-4.1-mini`, `gpt-5-mini`, **`gpt-5.4-nano`** o `gpt-5.4-mini`.

## Decisión

**Autenticación sin claves.** `DefaultAzureCredential`: en mi máquina usa `az login`; en Azure, la identidad
administrada de la Container App, con el rol *Foundry User* **solo sobre el proyecto**. El único código que
importa el SDK de Azure está en `app/foundry/`; el dominio depende de un `Protocol` (`GeneradorTexto`), por lo que
la lógica se prueba sin red con un generador falso.

**Modelo.** Deployment `mirob-chat` de **`gpt-5.4-nano` (2026-03-17)**, tipo **GlobalStandard** (pago por token; nunca
Provisioned, que cobra por hora) y cuota de 10.000 tokens por minuto. El nombre del deployment es genérico: cambiar
de modelo no exige tocar la configuración. `gpt-4o-mini` y `gpt-4.1-mini` están *Deprecated* y una suscripción que
nunca los desplegó no puede crearlos; `gpt-5-mini` retira en febrero de 2027.

**Llamada.** API Responses, `max_output_tokens=250`, timeout de 30 s y `store=False`. Son modelos de
razonamiento: `temperature` y `reasoning.effort` son **opcionales** (variables de entorno) y no se envían por defecto,
porque con razonamiento activo el modelo puede rechazar `temperature` y cuenta los tokens de "pensar" dentro del tope.
Si el modelo no devuelve texto, el bot responde un mensaje fijo y los tokens gastados igual se cuentan.

**Comportamiento (prompt de cada tenant).** Se le dan fragmentos delimitados entre `<contexto>` y `</contexto>` (con las
etiquetas sanitizadas) y se instruye, tras medirlo con el modelo real:
- usar **solo** esa información y **no suponer** capacidades, sectores ni casos que no figuren de forma explícita
  (antes respondía "sí" a "¿hacen sistemas para hospitales?" sin que el contenido lo dijera);
- responder una **frase exacta** cuando no alcanza ("No tenemos esa información…") y no explicar dónde buscó
  (evita "en los fragmentos que compartiste");
- responder en **texto plano**, sin Markdown (el chat no lo interpreta).

## Consecuencias

**Positivas**

- Ninguna clave de Foundry en el código, el entorno ni el repositorio.
- Respuestas fieles al contenido y consistentes entre tenants; costo por respuesta de unos 400 a 700 tokens.

**Negativas / límites conocidos**

- **El modelo se retira el 2027-09-21.** Standard y Global Standard se actualizan solos según
  `versionUpgradeOption` (hoy `OnceNewDefaultVersionAvailable`: puede cambiar de versión sin aviso; conviene
  `OnceCurrentVersionExpired` para controlarlo).
- Latencia de 1,4 s con el modelo caliente y hasta 6 s en la primera llamada (token de Entra ID).
- Un prompt estricto vuelve al bot más conservador: una pregunta cuya respuesta está solo implícita en el contenido
  recibe "no tenemos esa información". Es el costo de no inventar.
- Los nombres de los roles de Foundry cambiaron (*Azure AI User* es ahora *Foundry User*): se usa el GUID del rol.

## Seguimiento

- Fijar `versionUpgradeOption` en `OnceCurrentVersionExpired`.
- Evals con golden set, incluidas preguntas de *prompt injection* (Parte 2.3 y 2.5).
- Embeddings semánticos para reemplazar al retrieval léxico (Parte 2.1).
