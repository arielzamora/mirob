// Valores del despliegue de producción. Lo que cambia en cada deploy (tag de la imagen) y lo secreto
// (clave del admin) llegan por variables de entorno del pipeline: acá no hay ningún secreto.
using './main.bicep'

param foundryAccountName = 'mirob-foundry-arielz'
param foundryProjectName = 'mirob'
param chatDeploymentName = 'mirob-chat'

param imageTag = readEnvironmentVariable('IMAGE_TAG', 'latest')
param adminApiKey = readEnvironmentVariable('MIROB_ADMIN_API_KEY', '')
param reasoningEffort = readEnvironmentVariable('FOUNDRY_REASONING_EFFORT', '')
