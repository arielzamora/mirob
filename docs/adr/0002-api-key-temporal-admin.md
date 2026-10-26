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

## Enmienda (2026-10-26): la misma clave es el minilogin del panel

- **Rate limiting:** ya existe. El admin acepta 10 pedidos por minuto por IP (`MIROB_RATE_ADMIN_PER_MINUTE`), así que adivinar la
  clave por internet es inviable aunque sea una frase elegida por mí y no una cadena aleatoria. Debe ser larga (varias
  palabras al azar o 20+ caracteres) y no contener datos personales.
- **El front ya no exhibe el panel.** Quien abre la URL sin `embed=1` ve una portada neutra. El panel (cargar contenido, access
  keys, selector de modo) solo aparece tras un minilogin al que se llega con `#admin` al final de la URL, y que **valida el
  servidor**: un `GET /admin/keys` que responde 200 únicamente con la clave correcta. Nada se compara en el navegador, así que
  la clave no queda escrita en el JavaScript, que baja cualquiera (el repositorio es público). No es una defensa nueva, sino
  una pantalla menos expuesta; la protección real sigue siendo la API.
- Los enlaces de reclutadores y el widget (`embed=1`) no pasan por este login.

## Seguimiento

- Parte 2.5: reemplazarla por autenticación propia (y rate limiting).
- Parte 3.0: el admin usará autenticación fuerte; nunca una access key de reclutador.
