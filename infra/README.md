# Infraestructura y deploy de MiRob

Todo se despliega con GitHub Actions ([.github/workflows/deploy.yml](../.github/workflows/deploy.yml)) en cada push a `main`:

```
tests (ci.yml) → imagen Docker en GHCR → Bicep (rg-mirob) → front en Static Web Apps → smoke test
```

Si los tests fallan, no se publica nada. El Bicep ([main.bicep](main.bicep)) define el backend (Container Apps), el share de Azure Files con el contenido, el front (Static Web Apps), los logs y el permiso de la identidad del backend sobre Foundry. El recurso de Foundry, su proyecto y el modelo **no** los toca: se crearon a mano (`rg-mirob`, `mirob-foundry-arielz`, proyecto `mirob`, deployment `mirob-chat`).

## Preparación (se hace una sola vez)

### 1. Que GitHub pueda entrar a Azure sin claves (OIDC)

GitHub pide a Azure un token de corta vida y Azure lo acepta porque creamos esta confianza. No se guarda ninguna clave de Azure en el repo.

```powershell
$sub = az account show --query id -o tsv
$tenant = az account show --query tenantId -o tsv
$app = az ad app create --display-name "mirob-github-deploy" --query "{id:id, appId:appId}" -o json | ConvertFrom-Json
az ad sp create --id $app.appId
```

Credencial federada: solo el `environment` `production` de este repositorio puede usarla.

> **Ojo con el formato del `subject`.** GitHub ahora puede incluir los **ids numéricos** del dueño y del repositorio (`use_immutable_subject`), y entonces el token de OIDC trae `repo:<dueño>@<id>/<repo>@<id>:environment:production` en vez de `repo:<dueño>/<repo>:environment:production`. Si la credencial no coincide exactamente, el login falla con `AADSTS700213`. Para ver el prefijo que usa tu repositorio:
>
> ```bash
> gh api repos/arielzamora/mirob/actions/oidc/customization/sub --jq .sub_claim_prefix
> ```
>
> El `subject` de abajo es ese prefijo + `:environment:production`. Con ids es más seguro: no sirve si alguien recrea un repositorio con el mismo nombre.

Guardá este JSON como `credencial.json` (cambiá el `subject` por el de tu repositorio) y borralo después:

```json
{
  "name": "github-production",
  "issuer": "https://token.actions.githubusercontent.com",
  "subject": "repo:arielzamora@54455719/mirob@1430503058:environment:production",
  "audiences": ["api://AzureADTokenExchange"]
}
```

```powershell
az ad app federated-credential create --id $app.id --parameters credencial.json
```

Permisos, **solo sobre el grupo `rg-mirob`** (mínimo privilegio). *Contributor* crea los recursos; *Role Based Access Control Administrator* permite que el Bicep asigne el rol Foundry User al backend:

```powershell
$rg = "/subscriptions/$sub/resourceGroups/rg-mirob"
az role assignment create --assignee $app.appId --role "Contributor" --scope $rg
az role assignment create --assignee $app.appId --role "Role Based Access Control Administrator" --scope $rg
```

### 2. Configurar GitHub

En el repositorio → *Settings*:

1. **Environments → New environment → `production`**. Opcional: *Required reviewers* para aprobar cada deploy a mano.
2. **Secrets and variables → Actions → Variables** (no son secretas): `AZURE_CLIENT_ID` (el `appId` de arriba), `AZURE_TENANT_ID` y `AZURE_SUBSCRIPTION_ID`. Opcional: `FOUNDRY_REASONING_EFFORT` (por ejemplo `minimal`).
3. **Environments → production → Environment secrets**: `MIROB_ADMIN_API_KEY` (generala con `python -c "import secrets; print(secrets.token_urlsafe(32))"`). Es la clave de `/admin/content` y `/admin/reindex`.

### 3. Primer deploy

Hacé push a `main` (o *Actions → Deploy → Run workflow*). Los paquetes nuevos de GHCR nacen **privados** y Container Apps no puede bajarlos: la primera vez el job de infraestructura va a fallar al arrancar el contenedor. Entonces:

1. En GitHub: tu perfil → *Packages* → `mirob-backend` → *Package settings* → *Change visibility* → **Public**. (La imagen no contiene datos reales ni secretos.)
2. En *Actions*, **Re-run failed jobs**.

## Subir el contenido de los tenants

El contenido real no está en el repo ni en la imagen: vive en el share `mirob-data` (Azure Files), con una carpeta por tenant (`ariel-zamora/`, `digital-zur/`).

```powershell
$rg = "rg-mirob"
$st = az storage account list -g $rg --query "[0].name" -o tsv
$key = az storage account keys list -n $st -g $rg --query "[0].value" -o tsv
az storage file upload-batch --account-name $st --account-key $key --destination mirob-data --source backend\data\tenants
```

Después hay que avisarle al backend (indexa en memoria; no detecta cambios solo):

```powershell
curl -X POST https://<api>/admin/reindex -H "X-API-Key: <tu-clave>"
```

La URL de la API (`<api>`) está en el log del job *Desplegar infraestructura* y en el portal (*Container Apps → ca-mirob-api*).

## Publicar el widget en digitalzur.com.ar

1. En `backend/tenants/digital-zur/tenant.json`, poné los dominios del sitio en `origenes_permitidos` (por ejemplo `["https://www.digitalzur.com.ar", "https://digitalzur.com.ar"]`) y hacé push: sale un deploy nuevo.
2. En el sitio, embebé `https://<api>/embed/digital-zur` en un `<iframe>` (ver [frontend/README.md](../frontend/README.md)).

## Operación

| Qué | Cómo |
|---|---|
| Volver a una versión anterior | *Actions → Deploy → Run workflow* sobre un commit viejo, o re-ejecutar un deploy anterior |
| Cambiar límites o tope diario | Variables de entorno de `ca-mirob-api` (ver `backend/.env.example`) y nuevo deploy |
| Ver logs | Portal → `log-mirob` (Log Analytics), tabla `ContainerAppConsoleLogs_CL` |
| Borrar todo lo de MiRob | `az group delete -n rg-mirob` (**también borra Foundry y el modelo**) |

## Límites conocidos

- Una sola réplica del backend (índice en memoria y límites por proceso). Ver [ADR 0003](../docs/adr/0003-multitenancy-y-aislamiento.md) y [0004](../docs/adr/0004-gobierno-de-costos-de-ia.md).
- Escala a cero: la primera consulta tras un rato sin tráfico tarda unos segundos más.
- El share se monta con la clave de la cuenta de almacenamiento (limitación de Container Apps con Azure Files).
- Las actions están fijadas por versión mayor (`@v4`), no por SHA; Dependabot propone las actualizaciones.
