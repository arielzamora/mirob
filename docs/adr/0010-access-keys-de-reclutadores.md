# ADR 0010 — Acceso de reclutadores con access keys, sesiones firmadas y cupos

- **Estado:** Aceptada
- **Contexto de etapa:** Etapa 3, Parte 3.0

## Contexto y problema

El tenant `ariel-zamora` responde sobre mi perfil real (experiencia, certificaciones, forma de trabajar y un dato de
contacto). No puede ser un chat abierto como el widget de un sitio, pero tampoco quiero pedirle a un reclutador que
cree una cuenta. Hace falta un acceso **por invitación**: algo que yo pueda dar, limitar en el tiempo, medir y cortar
cuando quiera, sin guardar datos personales de quien lo usa.

## Decisión

**Una access key por reclutador o empresa y por tenant.** Se genera desde el admin con `secrets.token_urlsafe(32)` y
tiene etiqueta (a quién se la di), vencimiento (14 días por defecto, máximo 365), estado (activa o revocada) y un
cupo de preguntas opcional. Una key de un tenant no abre otro.

**Solo se guarda el hash.** En disco queda el SHA-256 de la key, nunca su valor, que se muestra una única vez al crearla.
Para keys aleatorias de 256 bits un hash rápido alcanza (no se pueden adivinar ni probar con diccionario, a diferencia de
una contraseña de persona). Se compara en tiempo constante. Una key de otro tenant responde igual que una inexistente.

**Entrega por enlace, con la key en el fragmento:** `https://<front>/?tenant=ariel-zamora&embed=1#k=<key>`. El fragmento
(`#`) no viaja al servidor, así que no queda en logs ni en el header `Referer`. El front la saca de la URL antes de que
entre al historial y la canjea **una sola vez** por una sesión con `POST /api/v1/acceso`.

**Sesión firmada, en el header y no en una cookie.** La sesión es un token sin estado (`v1.<datos>.<firma HMAC-SHA256>`
con `MIROB_SESSION_SECRET`) que dura 2 horas y nunca más que la propia key. Viaja en `Authorization: Bearer`. Se descartó
la cookie `HttpOnly` del plan original porque el front (`azurestaticapps.net`) y la API (`azurecontainerapps.io`) son
**sitios distintos**: los navegadores bloquean o limitan las cookies entre sitios y Safari las bloquea siempre. El costo es
que el token no es `HttpOnly`; se compensa con su vida corta, la CSP del front y que no contiene la key. Si algún día
front y API comparten dominio, se puede volver a la cookie sin tocar el resto.

**La sesión no decide sola.** En cada pregunta el backend vuelve a mirar que la key siga vigente: **revocarla corta el
acceso al instante**, sin esperar a que venza la sesión.

**Cupo y límites, solo contadores.** Cada pregunta gasta una del cupo (la comprobación y la suma son atómicas) y cada key
tiene su propio límite por minuto, aparte del límite por IP. Se guardan números y fechas: preguntas usadas, cantidad de
entradas, última entrada y última pregunta. **Nunca el texto de las preguntas ni de las respuestas.**

**Dónde vive.** Un JSON en el share de Azure Files, en `_keys/claves.json` (la carpeta empieza con `_`: un `tenant_id`
nunca puede empezar así). Se escribe a un archivo temporal y se renombra, bajo un lock. No hace falta ningún recurso nuevo.

**Administración.** `POST /admin/keys` (devuelve la key una vez y el enlace), `GET /admin/keys` y
`POST /admin/keys/{id}/revoke`, con la API key del admin, y la pestaña "Access keys" del front.

**Un tenant elige su modo** en `tenant.json`: `"acceso": "publico"` (por defecto) o `"access_key"`. Un tenant privado
**falla cerrado**: sin `MIROB_SESSION_SECRET` responde 503, no queda abierto.

## Opciones descartadas

- **Chat abierto:** expone mi perfil real a cualquiera y a cualquier costo de tokens.
- **Login con LinkedIn u otra identidad:** obliga al reclutador a registrarse y me hace tratar datos personales suyos.
- **Cookie `HttpOnly`:** no funciona entre sitios distintos (ver arriba).
- **JWT de una librería o Entra ID para los reclutadores:** más dependencias y complejidad para algo que se resuelve con
  un HMAC y la biblioteca estándar.
- **Guardar las preguntas para ver qué se consulta:** se descartó a propósito; es un tema de datos personales que requiere
  asesoramiento antes de implementarse.

## Consecuencias

- Puedo mandar un enlace, ver cuánto se usó y revocarlo, sin guardar nada de lo que se preguntó.
- **El tope diario de tokens sigue en memoria** (ADR 0004) y se reinicia cuando el backend escala a cero. Los contadores
  de las keys, en cambio, sobreviven porque están en el share.
- **Una sola réplica:** el JSON y los límites por minuto asumen un único proceso. Con más réplicas habría que pasar las
  keys a una tabla de Azure y los límites a un almacén compartido.
- La key viaja en un enlace: quien lo reenvíe da acceso hasta que venza o se revoque. Por eso el vencimiento es corto por
  defecto y hay cupo.
- El admin sigue protegido por la API key compartida (ADR 0002). Un login propio para el admin quedó fuera de alcance.
- El enlace con la key no debe pegarse en sitios públicos. Si se filtra, se revoca desde el admin.
