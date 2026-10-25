"""Registro de tenants (clientes) de MiRob: validación del id y configuración de cada uno.

Cada tenant es una carpeta versionada en backend/tenants/<tenant_id>/ con:
  - tenant.json : nombre, umbral de similitud, orígenes permitidos, si está habilitado.
  - prompt.md   : el system prompt propio del tenant (lo usa el modo Foundry).

El CONTENIDO (los .md/.txt que se indexan) NO vive acá: está en backend/data/ y
no se versiona (ver app/reindex.py). Acá solo hay configuración, sin datos personales.

Analogía .NET: es como un IOptionsMonitor<TenantOptions> por cliente, con una
lista blanca de tenants conocidos. El tenant_id llega del cliente (dato NO
confiable) y se usa para armar rutas de disco, por eso se valida dos veces:
primero el formato (regex) y después que exista en el registro.
"""

import json
import re
from dataclasses import dataclass
from pathlib import Path

from app.origenes import validar_origen

# backend/app/tenants.py -> .parent (app/) -> .parent (backend/) -> tenants/
TENANTS_DIR = Path(__file__).parent.parent / "tenants"

# Formato del id: minúsculas y números separados por guiones simples
# ("ariel-zamora", "digital-zur"). Sin "/", "\" ni "." → no hay forma de salir
# de la carpeta con un id como "../x" (path traversal).
_TENANT_ID_VALIDO = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
LARGO_MAXIMO_TENANT_ID = 40

# Umbral por defecto de similitud coseno para TF-IDF. Los scores reales de TF-IDF
# en preguntas bien respondidas están entre 0.15 y 0.80 (medido sobre el perfil
# ficticio y el contenido de digital-zur, con stopwords filtradas); 0.65 cortaría casi todo.
# Con las stopwords filtradas, una pregunta fuera de tema suele dar 0.000. Cada tenant puede
# ajustar el suyo: calibralo con preguntas reales de ese contenido.
UMBRAL_SIMILITUD_POR_DEFECTO = 0.10

# Quién puede conversar con el tenant: cualquiera ("publico", el widget de un sitio) o solo quien
# tenga una access key vigente ("access_key", por ejemplo reclutadores; ver app/accesskeys.py).
ACCESO_PUBLICO = "publico"
ACCESO_POR_KEY = "access_key"
_ACCESOS_VALIDOS = (ACCESO_PUBLICO, ACCESO_POR_KEY)
LARGO_MAXIMO_CONTACTO_ACCESO = 300


class TenantInvalido(ValueError):
    """El tenant_id tiene un formato inválido (se responde 422)."""


class TenantNoEncontrado(LookupError):
    """El tenant no existe o está deshabilitado (se responde 404)."""


class TenantConfigError(ValueError):
    """La configuración versionada de un tenant está rota (error del servidor, no del cliente)."""


# frozen=True: inmutable, como un `record` de C#.
@dataclass(frozen=True)
class TenantConfig:
    """Configuración de un tenant, ya validada."""

    tenant_id: str
    nombre: str  # nombre para mostrar en la UI
    system_prompt: str  # instrucciones del LLM, específicas del tenant
    umbral_similitud: float  # por debajo de este score NO se invoca al LLM
    origenes_permitidos: tuple[str, ...]  # sitios que pueden embeber el widget (frame-ancestors, ver /embed)
    usa_sample: bool  # True: el contenido sale de data/sample/ (perfil ficticio)
    tope_diario_tokens: int | None = None  # tope diario propio del tenant; None = solo rige el global
    acceso: str = ACCESO_PUBLICO  # "publico" o "access_key" (chat solo con una key vigente)
    # Texto para quien llega con una key vencida o revocada (cómo pedir una nueva). Lo escribe el dueño.
    contacto_acceso: str | None = None


def validar_tenant_id(tenant_id: str) -> str:
    """Devuelve el id si tiene formato válido; si no, lanza TenantInvalido."""
    # fullmatch exige que TODO el texto cumpla el patrón (como un ^...$ completo).
    if not tenant_id or len(tenant_id) > LARGO_MAXIMO_TENANT_ID or not _TENANT_ID_VALIDO.fullmatch(tenant_id):
        raise TenantInvalido(
            "tenant_id inválido: usá minúsculas, números y guiones (por ejemplo 'digital-zur'), "
            f"hasta {LARGO_MAXIMO_TENANT_ID} caracteres."
        )
    return tenant_id


def _cargar_config(carpeta: Path) -> tuple[TenantConfig, bool]:
    """Lee tenant.json y prompt.md de `carpeta`. Devuelve (config, habilitado)."""
    try:
        datos = json.loads((carpeta / "tenant.json").read_text(encoding="utf-8"))
        system_prompt = (carpeta / "prompt.md").read_text(encoding="utf-8").strip()
    except (OSError, json.JSONDecodeError) as error:
        raise TenantConfigError(f"Configuración ilegible del tenant '{carpeta.name}': {error}") from error

    umbral = float(datos.get("umbral_similitud", UMBRAL_SIMILITUD_POR_DEFECTO))
    if not 0.0 <= umbral <= 1.0:
        raise TenantConfigError(f"umbral_similitud de '{carpeta.name}' debe estar entre 0 y 1 (es {umbral}).")
    if not system_prompt:
        raise TenantConfigError(f"El prompt.md de '{carpeta.name}' está vacío.")
    if not datos.get("nombre"):
        raise TenantConfigError(f"Falta 'nombre' en el tenant.json de '{carpeta.name}'.")

    try:
        origenes = tuple(validar_origen(origen) for origen in datos.get("origenes_permitidos", []))
    except ValueError as error:
        raise TenantConfigError(f"origenes_permitidos de '{carpeta.name}': {error}") from error

    tope = datos.get("tope_diario_tokens")
    if tope is not None and (not isinstance(tope, int) or isinstance(tope, bool) or tope <= 0):
        raise TenantConfigError(f"tope_diario_tokens de '{carpeta.name}' debe ser un entero positivo (es {tope!r}).")

    acceso = datos.get("acceso", ACCESO_PUBLICO)
    if acceso not in _ACCESOS_VALIDOS:
        raise TenantConfigError(f"acceso de '{carpeta.name}' debe ser uno de {_ACCESOS_VALIDOS} (es {acceso!r}).")

    contacto = datos.get("contacto_acceso")
    if contacto is not None and (not isinstance(contacto, str) or len(contacto) > LARGO_MAXIMO_CONTACTO_ACCESO):
        raise TenantConfigError(
            f"contacto_acceso de '{carpeta.name}' debe ser un texto de hasta {LARGO_MAXIMO_CONTACTO_ACCESO} caracteres."
        )
    if contacto is not None:
        contacto = contacto.strip() or None  # un texto en blanco equivale a no haberlo puesto

    config = TenantConfig(
        tenant_id=carpeta.name,
        nombre=datos["nombre"],
        system_prompt=system_prompt,
        umbral_similitud=umbral,
        origenes_permitidos=origenes,
        usa_sample=bool(datos.get("usa_sample", False)),
        tope_diario_tokens=tope,
        acceso=acceso,
        contacto_acceso=contacto,
    )
    return config, bool(datos.get("habilitado", True))


def obtener_tenant(tenant_id: str) -> TenantConfig:
    """Devuelve la configuración del tenant, o lanza TenantInvalido / TenantNoEncontrado."""
    # 1) Formato: ANTES de tocar el disco. Con esto el id ya no puede contener rutas.
    validar_tenant_id(tenant_id)

    # 2) Lista blanca: solo existe si hay carpeta versionada con su configuración.
    carpeta = TENANTS_DIR / tenant_id
    if not (carpeta / "tenant.json").is_file():
        raise TenantNoEncontrado(f"El tenant '{tenant_id}' no existe.")

    config, habilitado = _cargar_config(carpeta)
    # Un tenant deshabilitado se comporta como inexistente: no revela que está reservado.
    if not habilitado:
        raise TenantNoEncontrado(f"El tenant '{tenant_id}' no existe.")
    return config


def listar_tenants() -> list[TenantConfig]:
    """Todos los tenants habilitados, ordenados por id (para el selector de la UI)."""
    if not TENANTS_DIR.is_dir():
        return []
    tenants = []
    for carpeta in sorted(TENANTS_DIR.iterdir()):
        if (carpeta / "tenant.json").is_file():
            config, habilitado = _cargar_config(carpeta)
            if habilitado:
                tenants.append(config)
    return tenants
