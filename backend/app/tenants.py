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
from dataclasses import dataclass, replace
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


PESO_SEMANTICO_POR_DEFECTO = 0.7
RETRIEVALS_VALIDOS = ("lexico", "semantico", "hibrido")


@dataclass(frozen=True)
class ConfigHibrido:
    """Cómo se combinan los dos puntajes en el modo híbrido (ADR 0011).

    El coseno de TF-IDF y el de embeddings viven en escalas distintas (el de embeddings casi nunca baja de ~0,2 aunque
    el texto no tenga relación), así que antes de combinarlos cada uno se lleva a [0, 1]: `piso` es lo que vale "nada
    relacionado" (da 0) y `techo` lo que vale "muy relacionado" (da 1). Los dos salen de la calibración del tenant.
    `umbral` está en esa escala normalizada.
    """

    umbral: float
    peso_semantico: float  # 0 = solo léxico, 1 = solo semántico; el léxico pesa (1 - peso_semantico)
    piso_lexico: float
    techo_lexico: float
    piso_semantico: float
    techo_semantico: float


@dataclass(frozen=True)
class ConfigRetrieval:
    """Umbrales de los retrievals que usan embeddings (el léxico usa `TenantConfig.umbral_similitud`).

    La escala del coseno con embeddings no se parece a la de TF-IDF (ADR 0001), así que cada modo lleva su propio
    umbral, calibrado por tenant. `None` = ese modo todavía no se calibró y NO está disponible: el chat responde
    con el retrieval léxico en su lugar.
    """

    umbral_semantico: float | None = None
    hibrido: ConfigHibrido | None = None
    # Con qué retrieval responde el tenant cuando el pedido no dice nada (el widget público no lo manda). Solo puede
    # ser un modo ya calibrado; el léxico siempre está disponible y es el default hasta calibrar (ADR 0011).
    por_defecto: str = "lexico"


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
    retrieval: ConfigRetrieval = ConfigRetrieval()  # umbrales de los modos con embeddings (opcional)


def _numero(valor: object, minimo: float, maximo: float) -> float | None:
    """El valor como float si es un número (no un bool) dentro de [minimo, maximo]; si no, None."""
    # bool es subclase de int en Python: se descarta a propósito (true/false no es un número de configuración).
    if not isinstance(valor, (int, float)) or isinstance(valor, bool) or not minimo <= valor <= maximo:
        return None
    return float(valor)


def _cargar_hibrido(datos: object, nombre_tenant: str) -> ConfigHibrido:
    """Lee retrieval.hibrido: umbral, peso_semantico (opcional) y los pisos/techos de normalización."""
    ruta = f"retrieval.hibrido de '{nombre_tenant}'"
    if not isinstance(datos, dict):
        raise TenantConfigError(f"{ruta} debe ser un objeto.")

    umbral = _numero(datos.get("umbral"), 0.0, 1.0)
    if umbral is None:
        raise TenantConfigError(f"{ruta}: 'umbral' debe ser un número entre 0 y 1.")
    peso = _numero(datos.get("peso_semantico", PESO_SEMANTICO_POR_DEFECTO), 0.0, 1.0)
    if peso is None:
        raise TenantConfigError(f"{ruta}: 'peso_semantico' debe ser un número entre 0 y 1.")

    # Los cosenos van de -1 a 1; en la práctica de 0 a 1, pero un modelo puede dar valores levemente negativos.
    limites = {}
    for senal in ("lexico", "semantico"):
        piso = _numero(datos.get(f"piso_{senal}"), -1.0, 1.0)
        techo = _numero(datos.get(f"techo_{senal}"), -1.0, 1.0)
        if piso is None or techo is None:
            raise TenantConfigError(f"{ruta}: 'piso_{senal}' y 'techo_{senal}' deben ser números entre -1 y 1.")
        if techo <= piso:
            raise TenantConfigError(f"{ruta}: 'techo_{senal}' ({techo}) debe ser mayor que 'piso_{senal}' ({piso}).")
        limites[senal] = (piso, techo)

    return ConfigHibrido(
        umbral=umbral,
        peso_semantico=peso,
        piso_lexico=limites["lexico"][0],
        techo_lexico=limites["lexico"][1],
        piso_semantico=limites["semantico"][0],
        techo_semantico=limites["semantico"][1],
    )


def _cargar_retrieval(datos: object, nombre_tenant: str) -> ConfigRetrieval:
    """Lee el bloque opcional `retrieval` de tenant.json: {"semantico": {"umbral": 0.45}, "hibrido": {...}}."""
    if datos is None:
        return ConfigRetrieval()
    if not isinstance(datos, dict):
        raise TenantConfigError(f"retrieval de '{nombre_tenant}' debe ser un objeto.")

    umbral_semantico = None
    semantico = datos.get("semantico")
    if semantico is not None:
        umbral_semantico = _numero(semantico.get("umbral") if isinstance(semantico, dict) else None, 0.0, 1.0)
        if umbral_semantico is None:
            raise TenantConfigError(f"retrieval.semantico.umbral de '{nombre_tenant}' debe ser un número entre 0 y 1.")

    hibrido = datos.get("hibrido")
    config = ConfigRetrieval(
        umbral_semantico=umbral_semantico,
        hibrido=None if hibrido is None else _cargar_hibrido(hibrido, nombre_tenant),
    )

    por_defecto = datos.get("por_defecto", "lexico")
    if por_defecto not in RETRIEVALS_VALIDOS:
        raise TenantConfigError(f"retrieval.por_defecto de '{nombre_tenant}' debe ser uno de {RETRIEVALS_VALIDOS}.")
    # Un default sin calibrar dejaría al tenant respondiendo siempre con el léxico sin que la config lo diga: se avisa.
    sin_calibrar = (por_defecto == "semantico" and umbral_semantico is None) or (
        por_defecto == "hibrido" and config.hibrido is None
    )
    if sin_calibrar:
        raise TenantConfigError(
            f"retrieval.por_defecto de '{nombre_tenant}' es '{por_defecto}', pero ese modo no tiene su bloque calibrado."
        )
    return replace(config, por_defecto=por_defecto)


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
        retrieval=_cargar_retrieval(datos.get("retrieval"), carpeta.name),
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
