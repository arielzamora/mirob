# ADR 0004 — Gobierno de costos de IA

- **Estado:** Aceptada (enmendada el 2026-10-23: cambio de modelo, ver punto 4)
- **Contexto de etapa:** Etapa 2, salida a producción del MVP (parte de la Parte 2.0 y adelanta lo básico de la 2.5)

## Contexto y problema

El chat de `digital-zur` y el de `ariel-zamora` van a ser públicos y cada respuesta del modo
`foundry` cuesta tokens. Sin controles, un bot de internet o una ráfaga de pedidos puede
generar gasto sin límite, y además la API de otro modo gratuito (raw/humanized) también
necesita protección contra abuso. El objetivo es **minimizar los tokens por consulta** y
**acotar el peor caso**.

## Decisión

Capas ordenadas de la más barata a la más cara:

1. **Entrada acotada:** `message` de hasta 350 caracteres, historial de hasta 20 mensajes de
   1000 caracteres (validado con Pydantic → 422).
2. **Umbral de similitud por tenant (0 tokens):** si el mejor chunk no llega a
   `umbral_similitud`, no se llama al LLM: mensaje fijo de "no encontré información". El
   umbral por defecto es **0.20**, no 0.65: con TF-IDF las preguntas bien respondidas dan
   scores de 0.25 a 0.55 (medido sobre el perfil ficticio), y 0.65 cortaría casi todo. Con
   embeddings semánticos (Parte 2.1) se revisa.
3. **Contexto mínimo:** al LLM van como máximo 3 chunks (solo los que superan el umbral, de
   hasta 1500 caracteres) y los últimos 6 mensajes del historial (500 caracteres cada uno).
4. **Modelo chico y salida acotada:** un modelo chico en deployment **GlobalStandard** (pago por
   token, nunca Provisioned, que cobra por hora), `max_output_tokens=250`, timeout de 30 s y
   `store=False`.
   - *Enmienda:* la decisión original era `gpt-4o-mini`, pero está *Deprecated* (retiro 2027-04-14)
     y una suscripción que nunca lo desplegó no puede crear deployments nuevos (tampoco gpt-4.1-mini
     ni nano). Se usa **`gpt-5.4-nano`** (alternativa: `gpt-5.4-mini`), GA con retiro el 2027-09-21.
   - Son modelos de razonamiento: no aceptan `temperature` con razonamiento activo y cuentan los
     tokens de "pensar" dentro del tope de salida. Por eso `temperature` y `reasoning.effort` son
     **opcionales** (`FOUNDRY_TEMPERATURE`, `FOUNDRY_REASONING_EFFORT`) y no se envían por defecto.
     Si el modelo gasta todo el tope pensando y no devuelve texto, el bot responde un mensaje fijo
     ("No pude redactar una respuesta...") y los tokens gastados igual se cuentan en el tope diario.
5. **Rate limit por IP** (ventana deslizante, en memoria):
   - Chat: **20 pedidos por minuto** (cualquier modo).
   - LLM: **30 llamadas por hora**. Cuenta solo las llamadas que realmente van al modelo; las
     consultas bajo el umbral no consumen cupo.
   - Admin: **10 pedidos por minuto**, para frenar la fuerza bruta de la API key (ADR 0002).
   - Responde `429` con `Retry-After`.
6. **Tope diario de tokens:** **200.000 tokens por día UTC** en total, y opcionalmente un
   `tope_diario_tokens` por tenant para que un cliente no consuma el cupo de los demás. Al
   alcanzarlo, el modo foundry responde `429` con `Retry-After` hasta las 00:00 UTC; raw,
   humanized y las consultas bajo el umbral siguen funcionando.

Todos los valores se pueden cambiar por variables de entorno (`MIROB_RATE_CHAT_PER_MINUTE`,
`MIROB_RATE_FOUNDRY_PER_HOUR`, `MIROB_RATE_ADMIN_PER_MINUTE`, `MIROB_DAILY_TOKEN_CAP`); un
valor inválido hace fallar el arranque con un mensaje claro.

**IP del cliente:** por defecto es la de la conexión. Detrás de un proxy (Azure Container
Apps) hay que definir `MIROB_TRUST_PROXY=1`; entonces se usa el **último** valor de
`X-Forwarded-For` (el que agregó nuestro proxy). Los anteriores los escribe el cliente y se
pueden falsificar, así que no se usan. Sin `MIROB_TRUST_PROXY`, el header se ignora.

**Implementación:** los controles 5 y 6 son un decorador del generador
(`GeneradorConPresupuesto`) que cumple el mismo `Protocol` que el cliente de Foundry. El
dominio no cambia, y solo cuentan las llamadas reales al LLM.

## Opciones descartadas

- **Umbral fijo de 0.65:** no es compatible con TF-IDF (ver arriba).
- **Contar todos los pedidos foundry contra el límite del LLM:** penalizaría consultas que
  cuestan 0.
- **Confiar siempre en `X-Forwarded-For`:** cualquiera evadiría el límite cambiando el header.
- **Redis/almacén externo desde ya:** más infraestructura de la que justifica un MVP.

## Consecuencias

**Positivas**

- El peor caso diario está acotado y es conocido: con `gpt-5.4-nano` y 200.000 tokens/día el
  gasto máximo es del orden de centavos de dólar por día (precios de referencia de terceros, a
  confirmar en la calculadora de Azure).
- Las consultas fuera de tema no cuestan nada.
- Todo es configurable sin tocar código y está cubierto por tests con relojes falsos.

**Negativas / límites conocidos**

- **El estado vive en memoria del proceso:** con varias réplicas, cada una cuenta aparte (el
  tope real puede ser N veces mayor) y un reinicio reinicia los contadores. Para un tope
  exacto hace falta un almacén compartido.
- Llamadas simultáneas ya en curso pueden sobrepasar un poco el tope (se verifica antes y se
  anota después).
- Los límites por IP afectan a usuarios que comparten IP (oficinas, redes móviles).
- El tope es un corte duro: al alcanzarlo, el bot de foundry deja de responder hasta el día
  siguiente. Conviene sumar una alerta de presupuesto en Azure (Parte 2.0).
- No hay todavía métrica de costo por pregunta ni observabilidad (Parte 2.0).

## Seguimiento

- Alerta de presupuesto en Azure y observabilidad con OpenTelemetry (Parte 2.0).
- Parte 2.5: Content Safety / Prompt Shields y reemplazo de la API key del admin.
- Parte 3.1: almacén compartido para los contadores si se escala a más de una réplica.
