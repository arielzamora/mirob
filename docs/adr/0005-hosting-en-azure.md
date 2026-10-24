# ADR 0005 — Hosting en Azure: Container Apps, Azure Files y Static Web Apps

- **Estado:** Aceptada (desplegada y verificada en producción el 2026-10-23)
- **Contexto de etapa:** Etapa 3, Parte 3.1

## Contexto y problema

MiRob tiene poco tráfico (un widget en un sitio institucional y, a futuro, un enlace para
reclutadores), así que el costo fijo tiene que ser casi cero. Además:

- El **contenido de los tenants es privado**: no puede ir en el repositorio ni dentro de la imagen.
- El índice de búsqueda vive **en memoria** y se arma leyendo archivos `.md`/`.txt` de una carpeta.
- Foundry (el modelo) ya existe, creado a mano, y un deploy **nunca** debe poder romperlo.

## Opciones consideradas

**Dónde corre el backend:** App Service (cobra aunque no haya tráfico), AKS (desproporcionado),
Azure Functions (el índice en memoria y el arranque pesado de pandas/scikit-learn no encajan),
**Azure Container Apps** con escala a cero.

**Dónde vive el contenido:** dentro de la imagen (datos personales en una imagen), Blob Storage
(exige SDK y código para bajar y sincronizar), base de datos (innecesaria para texto plano),
**Azure Files montado como carpeta** (el backend lee con `Path.glob`, sin cambios de código).

**Dónde se publica la imagen:** Azure Container Registry (privado, unos USD 5 al mes) o **GHCR**
(público y gratis; la imagen no lleva datos reales ni secretos).

**Dónde va el front:** Vercel (el plan Hobby no permite uso comercial) o **Azure Static Web Apps**
(plan Free).

## Decisión

Todo en el grupo de recursos `rg-mirob`, definido en `infra/main.bicep`:

- **Container Apps:** una app (`ca-mirob-api`) de 0,25 vCPU y 0,5 GiB, con ingress HTTPS externo,
  probes sobre `/health` y **mínimo 0 / máximo 1 réplica**. Identidad administrada propia con el rol
  *Foundry User* sobre el proyecto (nada más).
- **Azure Files:** cuenta `Standard_LRS` y un share `mirob-data` de 5 GiB, montado en
  `/app/data/tenants` con `uid=1000,gid=1000` para el usuario sin privilegios de la imagen.
- **Static Web Apps (Free)** para el front, publicado desde el pipeline con un token.
- **Log Analytics** con tope de ingesta de 0,1 GB por día.
- **Foundry:** el recurso, el proyecto y el deployment se **referencian como `existing`**; el Bicep no
  los crea ni los modifica.
- La imagen se publica en **GHCR**, con el SHA del commit como tag.

## Consecuencias

**Positivas**

- Costo esperado del orden de unos pocos dólares al mes sin contar tokens (a confirmar en la
  calculadora de Azure); escala a cero cuando no hay tráfico.
- El contenido nunca viaja en la imagen ni en git.
- Un deploy no puede dañar Foundry.

**Negativas / límites conocidos**

- **Arranque en frío:** la primera consulta tras un rato sin tráfico tarda de 6 a 12 segundos
  (medido en producción). Subir a una réplica mínima cuesta unos USD 10 al mes.
- **Una sola réplica:** el índice y los contadores de límites son por proceso (ver ADR 0003 y 0004).
- El share se monta con la **clave de la cuenta de almacenamiento** (limitación de Container Apps).
- El paquete de GHCR nace **privado** y hay que ponerlo público una vez, o Container Apps no puede
  bajarlo (así ocurrió en el primer deploy).
- El contenido real **no está versionado**: la copia local y el share son las únicas copias. Hay que
  respaldarlo aparte (ver ADR 0009).

## Seguimiento

- Evaluar réplica mínima 1 si el arranque en frío molesta a los visitantes del widget.
- Dominio propio para la API (hoy es `*.azurecontainerapps.io`).
- Observabilidad con OpenTelemetry (Parte 2.0).
