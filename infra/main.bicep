// Infraestructura de MiRob (Parte 3.1). Se despliega sobre el grupo de recursos existente `rg-mirob`:
//   az deployment group create --resource-group rg-mirob --parameters infra/main.bicepparam
//
// Bicep es el equivalente en Azure de un .csproj declarativo para la infraestructura: describís el
// estado que querés y Azure calcula qué crear o cambiar. Volver a desplegar lo mismo no rompe nada
// (es idempotente).
//
// QUÉ CREA: Log Analytics, Storage con Azure Files, entorno y app de Container Apps (el backend) y
// Static Web Apps (el front), más el rol que deja al backend llamar a Foundry.
// QUÉ NO CREA: el recurso de Foundry, su proyecto ni el deployment del modelo. Se crearon a mano y acá
// solo se REFERENCIAN (`existing`), así un deploy nunca puede pisar o romper el modelo.

targetScope = 'resourceGroup'

// ---------------------------------------------------------------------------------------------
// Parámetros
// ---------------------------------------------------------------------------------------------

@description('Región de los recursos nuevos. Por defecto, la del grupo de recursos.')
param location string = resourceGroup().location

@description('Región de Static Web Apps (solo admite algunas: westus2, centralus, eastus2, westeurope, eastasia).')
param staticWebAppLocation string = 'eastus2'

@description('Nombre del recurso de Foundry que ya existe.')
param foundryAccountName string

@description('Nombre del proyecto de Foundry que ya existe.')
param foundryProjectName string

@description('Nombre del deployment de chat dentro del proyecto (el modelo que responde).')
param chatDeploymentName string = 'mirob-chat'

@description('Imagen del backend, sin tag. Está en GHCR (público); no contiene datos reales.')
param imageRepository string = 'ghcr.io/arielzamora/mirob-backend'

@description('Tag de la imagen a desplegar. El pipeline pasa el SHA del commit para que cada deploy sea trazable.')
param imageTag string

@description('Clave del admin (POST /admin/content y /admin/reindex). Si queda vacía, el admin responde 503 (falla cerrado).')
@secure()
param adminApiKey string = ''

@description('Esfuerzo de razonamiento del modelo (none, minimal, low...). Vacío = no se envía.')
param reasoningEffort string = ''

@description('Tope diario de ingesta de Log Analytics en GB (corta el gasto si algo loguea de más).')
param logDailyQuotaGb string = '0.1'

// ---------------------------------------------------------------------------------------------
// Variables
// ---------------------------------------------------------------------------------------------

// Los nombres de las cuentas de almacenamiento son únicos en todo Azure, 3 a 24 caracteres, solo
// minúsculas y números. uniqueString() deriva 13 caracteres estables del id del grupo de recursos.
var storageAccountName = 'stmirob${uniqueString(resourceGroup().id)}'
var fileShareName = 'mirob-data'

// Rol "Foundry User" (antes "Azure AI User"). Se usa el GUID y no el nombre porque el rename todavía
// se está propagando (lo recomienda la documentación de Microsoft).
var foundryUserRoleId = '53ca6127-db72-4b80-b1b0-d745d6d5456d'

var foundryEndpoint = 'https://${foundryAccountName}.services.ai.azure.com/api/projects/${foundryProjectName}'

// ---------------------------------------------------------------------------------------------
// Logs
// ---------------------------------------------------------------------------------------------

resource logs 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: 'log-mirob'
  location: location
  properties: {
    sku: { name: 'PerGB2018' }
    retentionInDays: 30
    // Si se pasa del tope diario, deja de ingerir hasta el día siguiente (no sigue cobrando).
    workspaceCapping: { dailyQuotaGb: json(logDailyQuotaGb) }
  }
}

// ---------------------------------------------------------------------------------------------
// Almacenamiento del contenido de los tenants (Azure Files)
// ---------------------------------------------------------------------------------------------

resource storage 'Microsoft.Storage/storageAccounts@2023-05-01' = {
  name: storageAccountName
  location: location
  kind: 'StorageV2'
  sku: { name: 'Standard_LRS' } // redundancia local: la más barata; el contenido original vive en tu máquina/git
  properties: {
    minimumTlsVersion: 'TLS1_2'
    supportsHttpsTrafficOnly: true
    allowBlobPublicAccess: false
    // Container Apps monta el share por SMB con la clave de la cuenta, así que la clave tiene que estar habilitada.
    allowSharedKeyAccess: true
  }
}

resource fileService 'Microsoft.Storage/storageAccounts/fileServices@2023-05-01' = {
  parent: storage
  name: 'default'
}

resource share 'Microsoft.Storage/storageAccounts/fileServices/shares@2023-05-01' = {
  parent: fileService
  name: fileShareName
  properties: {
    shareQuota: 5 // GiB. Alcanza de sobra para texto; también es un tope de gasto.
    enabledProtocols: 'SMB'
  }
}

// ---------------------------------------------------------------------------------------------
// Front: Static Web Apps (plan Free). El contenido se publica desde el pipeline con un token.
// ---------------------------------------------------------------------------------------------

resource staticWebApp 'Microsoft.Web/staticSites@2023-12-01' = {
  name: 'swa-mirob'
  location: staticWebAppLocation
  sku: {
    name: 'Free'
    tier: 'Free'
  }
  properties: {}
}

// ---------------------------------------------------------------------------------------------
// Backend: Container Apps
// ---------------------------------------------------------------------------------------------

resource environment 'Microsoft.App/managedEnvironments@2024-03-01' = {
  name: 'cae-mirob'
  location: location
  properties: {
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: {
        customerId: logs.properties.customerId
        sharedKey: logs.listKeys().primarySharedKey
      }
    }
  }
}

// Registra el share dentro del entorno, con el nombre `mirobdata`; la app lo monta como volumen.
resource environmentStorage 'Microsoft.App/managedEnvironments/storages@2024-03-01' = {
  parent: environment
  name: 'mirobdata'
  properties: {
    azureFile: {
      accountName: storage.name
      accountKey: storage.listKeys().keys[0].value
      shareName: share.name
      accessMode: 'ReadWrite'
    }
  }
}

// Variables de entorno base. La clave del admin y el esfuerzo de razonamiento se suman solo si vienen.
var baseEnv = [
  { name: 'FOUNDRY_ENDPOINT', value: foundryEndpoint }
  { name: 'FOUNDRY_CHAT_DEPLOYMENT', value: chatDeploymentName }
  // CORS: el que llama a la API desde el navegador es el front, así que su origen es el único permitido.
  { name: 'MIROB_CORS_ORIGINS', value: 'https://${staticWebApp.properties.defaultHostname}' }
  // La usa GET /embed/{tenant} para armar el iframe que carga el front.
  { name: 'MIROB_FRONTEND_URL', value: 'https://${staticWebApp.properties.defaultHostname}' }
  // Detrás del ingress de Container Apps, la IP real del cliente viaja en X-Forwarded-For (ver ADR 0004).
  { name: 'MIROB_TRUST_PROXY', value: '1' }
]
var adminEnv = empty(adminApiKey) ? [] : [
  { name: 'MIROB_ADMIN_API_KEY', secretRef: 'admin-api-key' }
]
var reasoningEnv = empty(reasoningEffort) ? [] : [
  { name: 'FOUNDRY_REASONING_EFFORT', value: reasoningEffort }
]

resource api 'Microsoft.App/containerApps@2024-03-01' = {
  name: 'ca-mirob-api'
  location: location
  // Identidad administrada: con ella el backend se autentica en Foundry sin ninguna clave
  // (DefaultAzureCredential la usa solo). Es el equivalente a una Managed Identity de App Service.
  identity: { type: 'SystemAssigned' }
  properties: {
    managedEnvironmentId: environment.id
    configuration: {
      activeRevisionsMode: 'Single'
      ingress: {
        external: true
        targetPort: 8000
        allowInsecure: false // HTTP se redirige a HTTPS
      }
      secrets: empty(adminApiKey) ? [] : [
        { name: 'admin-api-key', value: adminApiKey }
      ]
    }
    template: {
      containers: [
        {
          name: 'api'
          image: '${imageRepository}:${imageTag}'
          resources: {
            cpu: json('0.25')
            memory: '0.5Gi'
          }
          env: concat(baseEnv, adminEnv, reasoningEnv)
          volumeMounts: [
            // data/sample (perfil ficticio) viene en la imagen; los tenants reales, del share.
            { volumeName: 'tenants-data', mountPath: '/app/data/tenants' }
          ]
          probes: [
            {
              type: 'Liveness'
              httpGet: { path: '/health', port: 8000 }
              initialDelaySeconds: 10
              periodSeconds: 30
            }
            {
              type: 'Readiness'
              httpGet: { path: '/health', port: 8000 }
              periodSeconds: 10
            }
          ]
        }
      ]
      scale: {
        // Escala a cero: sin tráfico no se paga cómputo (la primera consulta tras un rato tarda unos
        // segundos más). Máximo UNA réplica: el índice vive en memoria y los contadores de límites
        // son por proceso (ver ADR 0003 y 0004).
        minReplicas: 0
        maxReplicas: 1
        rules: [
          {
            name: 'http'
            http: { metadata: { concurrentRequests: '20' } }
          }
        ]
      }
      volumes: [
        {
          name: 'tenants-data'
          storageType: 'AzureFile'
          storageName: environmentStorage.name
          // La imagen corre con el usuario 1000: sin esto, el share montado quedaría solo para root.
          mountOptions: 'uid=1000,gid=1000,file_mode=0660,dir_mode=0770'
        }
      ]
    }
  }
}

// ---------------------------------------------------------------------------------------------
// Permiso para llamar a Foundry
// ---------------------------------------------------------------------------------------------

resource foundry 'Microsoft.CognitiveServices/accounts@2025-06-01' existing = {
  name: foundryAccountName
}

resource foundryProject 'Microsoft.CognitiveServices/accounts/projects@2025-06-01' existing = {
  parent: foundry
  name: foundryProjectName
}

// Asigna "Foundry User" a la identidad del backend, solo sobre ESE proyecto (mínimo privilegio).
// El name es un GUID determinístico: volver a desplegar actualiza la misma asignación en vez de duplicarla.
resource apiFoundryUser 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: foundryProject
  name: guid(foundryProject.id, api.id, foundryUserRoleId)
  properties: {
    principalId: api.identity.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', foundryUserRoleId)
  }
}

// ---------------------------------------------------------------------------------------------
// Salidas: las usa el pipeline (URL de la API para compilar el front, nombres para publicar).
// ---------------------------------------------------------------------------------------------

output apiUrl string = 'https://${api.properties.configuration.ingress.fqdn}'
output apiFqdn string = api.properties.configuration.ingress.fqdn
output containerAppName string = api.name
output staticWebAppName string = staticWebApp.name
output staticWebAppUrl string = 'https://${staticWebApp.properties.defaultHostname}'
output storageAccountName string = storage.name
output fileShareName string = share.name
