# ADR 0002 — API key temporal para proteger el admin

- **Estado:** Aceptada (provisoria)
- **Contexto de etapa:** Etapa 1, Parte 1.1

## Contexto y problema

`POST /admin/content` escribe archivos en el servidor y reindexa el corpus. Estaba abierto:
cualquiera que alcanzara la API podía subir contenido que MiRob después diría en mi nombre.
El repo es público y habrá un deploy, así que no puede quedar así. Una auditoría también
mostró que el nombre del archivo subido se usaba sin validar para armar la ruta de destino
(path traversal).

## Opciones consideradas

1. **API key compartida en un header**, leída de una variable de entorno.
2. Autenticación con Entra ID u otro proveedor desde ya.
3. Dejarlo abierto y confiar en que CORS lo limita.

## Decisión

Opción 1, como solución **temporal**:

- Header `X-API-Key` comparado con `MIROB_ADMIN_API_KEY` usando `secrets.compare_digest`
  (tiempo constante).
- **Fail closed:** si la variable no está configurada, el admin responde 503 en lugar de
  quedar abierto.
- Se descarta la opción 3: CORS solo restringe navegadores, no `curl` ni scripts.
- La opción 2 queda para la Parte 2.5 del plan; hoy sería más complejidad de la que
  justifica una Etapa 1 sin deploy.
- Junto con la clave se endurece la subida: el nombre se valida con lista blanca y se
  rechaza (no se "limpia" en silencio), solo `.md`/`.txt`, UTF-8 y máximo 1 MB. Se validan
  nombre y contenido **antes** de escribir a disco.

## Consecuencias

**Positivas**

- Cierra el hueco más grave con muy poco código y sin dependencias nuevas.
- El front la pide en un campo y la guarda solo en memoria (no en `localStorage` ni en la URL).

**Negativas / límites conocidos**

- Una sola clave compartida: sin identidad por persona, sin rotación ni vencimiento, sin
  rate limiting. Si se filtra, hay que cambiarla a mano en el servidor.
- Viaja en cada request: solo es aceptable sobre HTTPS (en local, HTTP está bien).
- Un request sin clave igual llega a parsearse el cuerpo multipart antes de rechazarse;
  el tope de tamaño se aplica al leer el archivo, no antes.

## Seguimiento

- Parte 2.5: reemplazarla por autenticación propia (y rate limiting).
- Parte 3.0: el admin usará autenticación fuerte; nunca una access key de reclutador.
