# ADR 0003 — Multitenancy: aislamiento de datos y embebido por tenant

- **Estado:** Aceptada
- **Contexto de etapa:** Etapa 2, salida a producción del MVP

## Contexto y problema

MiRob nació como el bot de una sola persona. Ahora tiene que atender a varios clientes
(*tenants*): `ariel-zamora` (reclutadores), `digital-zur` (sitio institucional, embebido en
un `<iframe>`) y a futuro otros como `chocolateria`. Cada uno tiene su contenido, su tono y
su configuración, y **un tenant nunca debe ver los datos de otro**: ni en el retrieval, ni
en el prompt que viaja al LLM, ni en el disco.

## Decisión

**Aislamiento de datos**

- Cada tenant tiene su carpeta de contenido (`backend/data/tenants/<tenant_id>/`, ignorada
  por git) y su **propio índice TF-IDF en memoria**. No existe una matriz compartida: el
  retrieval de un tenant solo puede ver los chunks de su carpeta, y al LLM solo llegan esos.
- El `tenant_id` llega del cliente, así que es un dato **no confiable** que se usa para armar
  rutas. Se valida dos veces: formato (`[a-z0-9]+(-[a-z0-9]+)*`, máximo 40 caracteres, sin
  `/`, `\` ni `.`) y lista blanca (solo existen los tenants con carpeta versionada en
  `backend/tenants/`). Un tenant deshabilitado responde igual que uno inexistente (404).
- Sin respaldo silencioso: un tenant sin contenido responde 503 en vez de usar el de otro.
  Solo `demo` (declarado con `usa_sample`) lee el perfil ficticio, y es de solo lectura.
- `tenant_id` es **obligatorio** en `/api/v1/chat` y `/admin/content`; no hay tenant por defecto.

**Configuración por tenant (versionada, sin datos personales)**

`backend/tenants/<id>/tenant.json` (nombre, `umbral_similitud`, `origenes_permitidos`,
`tope_diario_tokens` opcional, `habilitado`) y `prompt.md` (system prompt propio).

**Qué sitios pueden embeber el widget (`frame-ancestors` por tenant)**

> La decisión completa del widget, la cadena de iframes y la corrección posterior está en el [ADR 0007](0007-widget-embebible-y-cadena-de-iframes.md); lo de acá es el resumen.

CORS y `frame-ancestors` responden preguntas distintas:

- **CORS** decide qué *origen* puede llamar a la API desde un navegador. Quien llama es el
  JavaScript del **front**, no el sitio que lo embebe, así que es una lista **global** (los
  orígenes del front: `MIROB_CORS_ORIGINS` + localhost). No puede ser por tenant.
- **`frame-ancestors`** decide qué sitios pueden meter la página en un iframe. Lo manda la
  página embebida, y un hosting estático (Static Web Apps) manda el mismo header para todos
  los tenants, así que no puede distinguir uno de otro.

Para que sea realmente por tenant, la API sirve `GET /embed/{tenant_id}`: un HTML mínimo con
`Content-Security-Policy: frame-ancestors <origenes_permitidos del tenant>` (o `'none'` si no
tiene ninguno) que adentro carga el front con `?tenant=<id>&embed=1`. El sitio del cliente
embebe **esa** URL.

> **Corrección (2026-10-23, descubierta al probar el widget en producción).** La primera versión de
> este ADR decía que el front debía permitir `frame-ancestors` **solo** a la API. Es incorrecto:
> los navegadores comprueban `frame-ancestors` contra **todos** los ancestros del iframe, no solo el
> padre inmediato. El front queda dentro de la página `/embed` (API), que a su vez queda dentro del
> sitio del cliente; para el front el "abuelo" es el sitio del cliente y, si no está en su lista, se
> niega a cargar ("ha rechazado la conexión").
>
> Decisión corregida: el front acepta como ancestros **la API más la unión de los
> `origenes_permitidos` de los tenants habilitados**, calculada en el pipeline a partir de los
> `tenant.json` (ver `deploy.yml`). Consecuencia: la restricción **por tenant** solo se aplica
> en la página `/embed`; un sitio permitido para un tenant podría cargar el front directo con
> `?tenant=<otro>`. Es una limitación aceptada: la API de chat es pública de todos modos (ver
> límites de este ADR) y la restricción busca evitar el embebido ajeno, no ocultar datos.
> Además, un tenant dinámico cuyos sitios no estén en un `tenant.json` versionado no entra en esa
> unión hasta el próximo deploy del front.

## Opciones descartadas

- **Un índice único con una columna `tenant_id` filtrada al buscar:** un error en un filtro
  mezclaría clientes. Con índices separados, la separación es estructural.
- **`frame-ancestors` global con la unión de todos los sitios:** cualquier sitio permitido
  para un tenant podría embeber a todos los demás.
- **Un front desplegado por tenant:** resuelve el header, pero multiplica los despliegues.
- **Validar solo en el front:** se saltea con `curl`; no es una barrera.

## Consecuencias

**Positivas**

- Aislamiento por construcción y fácil de testear (hay tests que suben contenido a dos
  tenants y verifican que ni las respuestas ni el prompt del LLM mezclan datos).
- Alta de un cliente = una carpeta con dos archivos + su contenido; sin tocar código.

**Negativas / límites conocidos**

- El `tenant_id` **no es un secreto** ni una credencial: quien conozca el id puede consultar
  ese tenant desde `curl`. Sirve para clientes cuyo contenido es público. Si un tenant llega a
  tener contenido privado, necesita autenticación propia (ver Parte 3.0 del plan).
- El front acepta como ancestros la unión de los sitios de todos los tenants (ver la corrección arriba).
- `frame-ancestors` protege contra el embebido en navegadores, no contra llamadas directas a
  la API; para eso están los límites de ADR 0004.
- `GET /api/v1/tenants` lista los tenants habilitados (id y nombre) a cualquiera.
- El contenido vive en disco local ignorado por git: un contenedor de Azure no lo tendría. Falta
  decidir dónde se guarda (Azure Files o Blob) antes del deploy (Parte 3.1).
- Los índices están en memoria: se reconstruyen al reiniciar y no se comparten entre réplicas.

## Seguimiento

- Parte 3.0: autenticación por tenant/reclutador para contenido privado.
- Parte 3.1: dónde persiste el contenido en Azure.
