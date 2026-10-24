# ADR 0006 — CI/CD con GitHub Actions y OIDC (sin claves)

- **Estado:** Aceptada (funcionando desde el 2026-10-23)
- **Contexto de etapa:** Etapa 3, Parte 3.1

## Contexto y problema

Quiero que cada cambio en `main` llegue a producción **solo si pasaron los tests**, sin guardar ninguna
clave de Azure en el repositorio y sin desplegar a mano.

## Opciones consideradas

1. **Service principal con secreto** guardado en GitHub (la clave caduca y puede filtrarse).
2. *Publish profile* o claves de cada recurso.
3. **OIDC federado**: GitHub emite un token firmado de corta vida y Azure lo acepta por una confianza
   configurada de antemano.
4. Desplegar a mano desde mi máquina.

## Decisión

**Opción 3.** Dos workflows en `.github/workflows/`:

- `ci.yml` (pull requests y reutilizable): tests del backend, lint y build del front, validación del
  Bicep y build de la imagen Docker con arranque y consulta a `/health`. No usa secretos ni toca Azure.
- `deploy.yml` (push a `main` o manual): llama a `ci.yml` y, **solo si pasa**, sigue: publica la imagen en GHCR
  → `what-if` y despliegue del Bicep → compila y publica el front → smoke test (`/health`, un chat en
  modo raw sobre `demo` y los headers de `/embed` y del front).

Detalles de seguridad:

- Una app registration (`mirob-github-deploy`) con **credencial federada** solo para el `environment`
  `production` de este repositorio, con permisos **únicamente sobre `rg-mirob`**: *Contributor* y *Role Based
  Access Control Administrator* (este último para que el Bicep asigne el rol Foundry User al backend).
- Permisos mínimos por job (`contents: read`; `packages: write` solo en el que publica; `id-token: write` solo en
  los que entran a Azure) y un solo deploy a la vez (`concurrency`).
- Variables no secretas: `AZURE_CLIENT_ID`, `AZURE_TENANT_ID`, `AZURE_SUBSCRIPTION_ID`. Secreto del
  environment: `MIROB_ADMIN_API_KEY`.
- Cada imagen se etiqueta con el SHA del commit: cada deploy es trazable y reversible.

## Lo que se aprendió al ponerlo en marcha

- **El `subject` del token incluye ids numéricos.** El repositorio tiene `use_immutable_subject`, así que GitHub
  presenta `repo:arielzamora@<id>/mirob@<id>:environment:production`. Una credencial creada con el formato
  viejo (`repo:arielzamora/mirob:...`) falla con `AADSTS700213`. El formato con ids es más seguro
  (no sirve si alguien recrea un repositorio con el mismo nombre).
- **El smoke test usaba un HEAD** contra una ruta que solo acepta GET: devolvía 405. Se cambió a un GET que
  vuelca solo los headers.

## Consecuencias

**Positivas**

- Ninguna clave de Azure en el repositorio; nada que rotar ni que se filtre.
- Un deploy roto por tests fallidos es imposible por construcción.

**Negativas / límites conocidos**

- Las actions se referencian por **versión mayor** (`@v4`), no por SHA. Dependabot propone las
  actualizaciones, pero los saltos de versión mayor requieren revisión (hay 4 pendientes).
- El environment `production` no exige aprobación manual ni restringe ramas todavía; conviene limitarlo a `main`.
- El primer deploy falla porque el paquete de GHCR nace privado (ver ADR 0005).
- Bicep despliega en modo incremental: no borra recursos que alguien cree a mano en el grupo.

## Seguimiento

- Restringir el environment `production` a la rama `main`.
- Sumar evals con umbral mínimo antes del deploy, cuando existan (Parte 2.3).
- Revisar los PR de Dependabot de a uno.
