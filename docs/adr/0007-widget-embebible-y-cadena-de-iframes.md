# ADR 0007 — Widget embebible: página `/embed`, cadena de iframes y `frame-ancestors`

- **Estado:** Aceptada (verificada en producción en `digitalzur.com.ar` el 2026-10-23)
- **Contexto de etapa:** Etapa 3, salida a producción

## Contexto y problema

El sitio de un cliente tiene que poder mostrar el chat de **su** tenant en un `<iframe>`, y **otros sitios no**
deberían poder embeberlo. Eso se controla con el header `Content-Security-Policy: frame-ancestors` que manda la
página embebida. El front vive en un hosting estático que manda el **mismo** header para todos los tenants.

## Opciones consideradas

1. **Rutas por tenant en Static Web Apps** con su propio header: se generan en el pipeline y no admiten tenants
   creados en caliente (tendrían que esperar un deploy del front).
2. **Servir la interfaz del chat desde la propia API** (un solo salto, header por tenant dinámico): más
   trabajo (la imagen incluiría el build del front) y se descartó por ahora.
3. **Una página `/embed/{tenant}` en la API** que manda el `frame-ancestors` de ESE tenant y carga el front
   en un iframe interno.
4. Un `frame-ancestors https:` abierto: permitiría *clickjacking* sobre las pantallas de administración, que están en
   el mismo front.

## Decisión

**Opción 3**, con esta cadena de tres sitios:

```
sitio del cliente  →  API  /embed/<tenant>  →  front (Static Web Apps)
   (abuelo)            (padre, CSP por tenant)      (CSP: la API y los sitios de los tenants)
```

- `GET /embed/{tenant_id}` devuelve un HTML mínimo (CSP `default-src 'none'`) con el `frame-ancestors` igual a los
  `origenes_permitidos` del tenant (`'none'` si no tiene) y un iframe al front con `?tenant=<id>&embed=1`.
  Los orígenes se validan con lista blanca (nada de comodines ni caracteres que permitan inyectar directivas).
- **El front acepta como ancestros la API y la unión de los sitios de todos los tenants habilitados.** Esa lista la
  calcula el pipeline desde los `tenant.json`. **Los navegadores comprueban `frame-ancestors` contra toda la cadena**,
  no solo contra el padre: la primera versión permitía solo a la API y el widget quedaba bloqueado
  (*"ha rechazado la conexión"*). Se detectó probando en producción y se reprodujo en una réplica local de tres
  orígenes antes de arreglarlo.
- `?header=0` oculta la barra interna del chat para el sitio que pone la suya.
- En el sitio del cliente, un componente propio (`chat-widget.tsx`) con botón flotante, saludo y encabezado. El
  iframe **se crea recién al abrir el chat**: quien no lo usa no despierta al backend, que escala a cero.

## Consecuencias

**Positivas**

- La restricción por tenant funciona en la página `/embed` y soporta tenants nuevos sin tocar el front.
- Sin dependencias de terceros en el sitio del cliente.

**Negativas / límites conocidos**

- **La restricción por tenant solo se aplica en `/embed`.** Un sitio permitido para un tenant podría cargar el front
  directo con `?tenant=<otro>`. Se acepta: la API de chat es pública de todos modos (ver ADR 0004) y la
  restricción evita el embebido ajeno, no oculta datos.
- Un tenant cuyos sitios no estén en un `tenant.json` versionado no entra en la lista del front hasta un deploy.
- Dos iframes anidados suman latencia y dependen del arranque en frío del backend.
- `frame-ancestors` no protege contra llamadas directas a la API: para eso están los límites del ADR 0004.

## Seguimiento

- Evaluar servir la interfaz desde la API (opción 2), que elimina el salto extra y la limitación de arriba.
- Dominio propio para la API y para el front.
