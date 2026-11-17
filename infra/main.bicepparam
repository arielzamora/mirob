// Valores del despliegue de producción. Lo que cambia en cada deploy (tag de la imagen) y lo secreto
// (clave del admin) llegan por variables de entorno del pipeline: acá no hay ningún secreto.
using './main.bicep'

param foundryAccountName = 'mirob-foundry-arielz'
param foundryProjectName = 'mirob'
param chatDeploymentName = 'mirob-chat'
// Se creó a mano en Foundry (mismo proyecto que el de chat); el Bicep solo lo referencia por nombre.
param embeddingsDeploymentName = 'text-embedding-3-small'

param imageTag = readEnvironmentVariable('IMAGE_TAG', 'latest')
param adminApiKey = readEnvironmentVariable('MIROB_ADMIN_API_KEY', '')
param sessionSecret = readEnvironmentVariable('MIROB_SESSION_SECRET', '')
param reasoningEffort = readEnvironmentVariable('FOUNDRY_REASONING_EFFORT', '')
