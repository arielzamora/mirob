"""Access keys de reclutadores: modelo, generación, validación y almacenamiento (Parte 3.0, grupo A).

Una access key es un enlace-secreto que le das a un reclutador o empresa para que pueda
conversar con el bot de un tenant (por ejemplo, ariel-zamora) sin que el chat quede abierto
al público. Cada key tiene etiqueta, vencimiento, estado (activa / revocada) y un cupo opcional.

Lo que se guarda y lo que NO:
  - Se guarda solo el HASH (SHA-256) de la key, nunca el valor. El valor se devuelve UNA vez, al
    crearla (en .NET sería como guardar un hash de la API key y mostrar la clave en claro una sola vez).
  - Para contraseñas de personas se usa un hash lento y con sal (bcrypt, argon2) porque son
    débiles y adivinables. Una key de secrets.token_urlsafe(32) tiene 256 bits al azar: no se puede
    adivinar ni probar con diccionario, así que un SHA-256 simple alcanza y es rápido de validar.

Este módulo no sabe nada de HTTP: es lógica y persistencia. El acceso por link, la cookie de sesión
y los límites por key vienen en los grupos siguientes. Es testeable con una carpeta temporal.
"""

import hashlib
import json
import os
import secrets
import tempfile
import threading
from collections.abc import Callable
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal, Protocol

from app.reindex import CONTENT_DIR
from app.tenants import validar_tenant_id

# Dónde vive el archivo de keys. Está junto al contenido (data/tenants/, que en Azure es el share de
# Azure Files) en una carpeta que empieza con "_": un tenant_id nunca puede empezar así (ver el regex
# de tenants.py), así que no se confunde con la carpeta de ningún tenant.
RUTA_POR_DEFECTO = CONTENT_DIR / "tenants" / "_keys" / "claves.json"

DIAS_VENCIMIENTO_POR_DEFECTO = 14
DIAS_VENCIMIENTO_MAXIMO = 365
LARGO_MAXIMO_ETIQUETA = 80
# Una key real mide ~43 caracteres. Más que esto es basura: se rechaza sin ni siquiera hashearla.
LARGO_MAXIMO_CLAVE = 200

EstadoClave = Literal["activa", "vencida", "revocada"]


# --- Errores -----------------------------------------------------------------


class AccessKeyError(Exception):
    """Base de los errores de este módulo."""


class ClaveInvalida(AccessKeyError):
    """La key no existe o no corresponde a ese tenant. Mismo error para ambos casos: no se revela cuál."""


class ClaveRevocada(AccessKeyError):
    """La key existe pero fue revocada a mano."""


class ClaveVencida(AccessKeyError):
    """La key existe pero ya pasó su fecha de vencimiento."""


class DatosInvalidos(AccessKeyError, ValueError):
    """Los datos para crear una key no cumplen las reglas (etiqueta vacía, vencimiento absurdo...)."""


class AlmacenClavesError(AccessKeyError):
    """El archivo de keys no se pudo leer o está corrupto. No se trata como "vacío": fallar es más seguro."""


# --- Modelo ------------------------------------------------------------------


# frozen=True: inmutable, como un `record` de C#. Para "modificar" una key se crea una copia con replace().
@dataclass(frozen=True)
class AccessKey:
    key_id: str  # identificador público corto: sirve para listar y revocar sin exponer la key
    tenant_id: str  # la key solo abre ESTE tenant
    etiqueta: str  # a quién se le dio (reclutador / empresa)
    hash_clave: str  # SHA-256 (hex) de la key
    creada_en: datetime  # siempre en UTC
    vence_en: datetime  # siempre en UTC
    revocada: bool = False
    cupo_preguntas: int | None = None  # None = sin cupo propio
    preguntas_usadas: int = 0  # contador; el grupo C lo incrementa (solo número, nunca el texto)

    def estado(self, ahora: datetime) -> EstadoClave:
        """La revocación manda sobre el vencimiento: una key revocada se muestra "revocada" aunque haya vencido."""
        if self.revocada:
            return "revocada"
        if ahora >= self.vence_en:
            return "vencida"
        return "activa"


def hashear_clave(clave: str) -> str:
    """SHA-256 de la key, en hexadecimal."""
    return hashlib.sha256(clave.encode("utf-8")).hexdigest()


def generar_clave() -> str:
    """Key nueva: 32 bytes al azar de un generador criptográfico, en base64 apto para URL (~43 caracteres)."""
    return secrets.token_urlsafe(32)


# --- Persistencia ------------------------------------------------------------


class AlmacenClaves(Protocol):
    """Contrato del almacén (en .NET: una interfaz IAccessKeyStore). Los tests y el servicio dependen de esto.

    Hoy hay una implementación en archivo JSON; si algún día hace falta otra (Azure Table Storage), el
    resto del código no cambia.
    """

    def listar(self) -> list[AccessKey]: ...

    def guardar(self, clave: AccessKey) -> None:
        """Inserta la key o, si ya existe una con el mismo key_id, la reemplaza."""
        ...


def _a_json(clave: AccessKey) -> dict:
    datos = asdict(clave)
    datos["creada_en"] = clave.creada_en.isoformat()
    datos["vence_en"] = clave.vence_en.isoformat()
    return datos


def _desde_json(datos: dict) -> AccessKey:
    return AccessKey(
        **{
            **datos,
            "creada_en": datetime.fromisoformat(datos["creada_en"]),
            "vence_en": datetime.fromisoformat(datos["vence_en"]),
        }
    )


class AlmacenClavesJson:
    """Guarda las keys en un archivo JSON. Pensado para pocas keys y una sola réplica del backend.

    - Un lock (threading.Lock, como `lock` en C#) evita que dos pedidos escriban a la vez.
    - Cada escritura va a un archivo temporal y después se renombra encima del original (os.replace):
      si el proceso se corta a mitad, queda el archivo viejo entero y no uno a medio escribir.
    """

    def __init__(self, ruta: Path = RUTA_POR_DEFECTO) -> None:
        self._ruta = ruta
        self._lock = threading.Lock()

    def listar(self) -> list[AccessKey]:
        with self._lock:
            return self._leer()

    def guardar(self, clave: AccessKey) -> None:
        with self._lock:
            claves = [c for c in self._leer() if c.key_id != clave.key_id]
            claves.append(clave)
            self._escribir(claves)

    def _leer(self) -> list[AccessKey]:
        if not self._ruta.is_file():
            return []  # primera vez: todavía no se creó ninguna key
        try:
            contenido = json.loads(self._ruta.read_text(encoding="utf-8"))
            return [_desde_json(item) for item in contenido["claves"]]
        except (OSError, ValueError, KeyError, TypeError) as error:
            # Sin el detalle del error: el JSON tiene hashes y etiquetas, no va a los logs ni a la respuesta.
            raise AlmacenClavesError("No se pudo leer el archivo de access keys.") from error

    def _escribir(self, claves: list[AccessKey]) -> None:
        self._ruta.parent.mkdir(parents=True, exist_ok=True)
        contenido = json.dumps({"claves": [_a_json(c) for c in claves]}, indent=2, ensure_ascii=False)
        # El temporal va en la MISMA carpeta: renombrar dentro del mismo volumen es atómico.
        descriptor, ruta_temporal = tempfile.mkstemp(dir=self._ruta.parent, suffix=".tmp")
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as archivo:
                archivo.write(contenido)
            os.replace(ruta_temporal, self._ruta)
        except OSError as error:
            Path(ruta_temporal).unlink(missing_ok=True)
            raise AlmacenClavesError("No se pudo guardar el archivo de access keys.") from error


# --- Servicio ----------------------------------------------------------------


def _ahora_utc() -> datetime:
    return datetime.now(timezone.utc)


class ServicioClaves:
    """Casos de uso sobre las keys: crear, validar, revocar y listar.

    Recibe el almacén y el reloj por el constructor (inyección de dependencias): los tests pasan una
    carpeta temporal y un reloj falso para simular el paso de los días sin esperar.
    """

    def __init__(self, almacen: AlmacenClaves, reloj: Callable[[], datetime] = _ahora_utc) -> None:
        self._almacen = almacen
        self._reloj = reloj

    def crear(
        self,
        tenant_id: str,
        etiqueta: str,
        dias_vigencia: int = DIAS_VENCIMIENTO_POR_DEFECTO,
        cupo_preguntas: int | None = None,
    ) -> tuple[AccessKey, str]:
        """Crea una key y devuelve (registro, key en claro). La key en claro NO se puede recuperar después."""
        try:
            tenant_id = validar_tenant_id(tenant_id)
        except ValueError as error:
            raise DatosInvalidos(str(error)) from error
        etiqueta = _validar_etiqueta(etiqueta)
        if not 1 <= dias_vigencia <= DIAS_VENCIMIENTO_MAXIMO:
            raise DatosInvalidos(f"El vencimiento debe ser de 1 a {DIAS_VENCIMIENTO_MAXIMO} días.")
        if cupo_preguntas is not None and cupo_preguntas < 1:
            raise DatosInvalidos("El cupo de preguntas debe ser un entero mayor a 0, o vacío para no limitar.")

        clave = generar_clave()
        ahora = self._reloj()
        registro = AccessKey(
            key_id=self._nuevo_id(),
            tenant_id=tenant_id,
            etiqueta=etiqueta,
            hash_clave=hashear_clave(clave),
            creada_en=ahora,
            vence_en=ahora + timedelta(days=dias_vigencia),
            cupo_preguntas=cupo_preguntas,
        )
        self._almacen.guardar(registro)
        return registro, clave

    def validar(self, clave: str, tenant_id: str) -> AccessKey:
        """Devuelve el registro si la key sirve para ese tenant; si no, lanza el error que corresponda.

        Compara en tiempo constante (secrets.compare_digest) contra cada hash, sin cortar en el primero que
        coincide, para no filtrar por cuánto tarda la respuesta si una key existe o no.
        """
        if not clave or len(clave) > LARGO_MAXIMO_CLAVE:
            raise ClaveInvalida("Access key inválida.")
        hash_recibido = hashear_clave(clave)
        encontrada: AccessKey | None = None
        for candidata in self._almacen.listar():
            if secrets.compare_digest(candidata.hash_clave, hash_recibido):
                encontrada = candidata

        # Una key de otro tenant responde igual que una inexistente: no se revela que existe.
        if encontrada is None or encontrada.tenant_id != tenant_id:
            raise ClaveInvalida("Access key inválida.")
        estado = encontrada.estado(self._reloj())
        if estado == "revocada":
            raise ClaveRevocada("La access key fue revocada.")
        if estado == "vencida":
            raise ClaveVencida("La access key venció.")
        return encontrada

    def verificar_vigente(self, key_id: str, tenant_id: str) -> AccessKey:
        """Comprueba que la key de una sesión ya abierta siga sirviendo (no revocada, no vencida).

        Se llama en cada pedido del chat: así revocar una key corta el acceso de inmediato, sin esperar
        a que venza la sesión. Una key que no existe o es de otro tenant lanza ClaveInvalida.
        """
        for clave in self._almacen.listar():
            if clave.key_id == key_id and clave.tenant_id == tenant_id:
                estado = clave.estado(self._reloj())
                if estado == "revocada":
                    raise ClaveRevocada("La access key fue revocada.")
                if estado == "vencida":
                    raise ClaveVencida("La access key venció.")
                return clave
        raise ClaveInvalida("Access key inválida.")

    def revocar(self, key_id: str) -> AccessKey:
        """Marca la key como revocada (no se borra: queda el registro). Revocar dos veces no falla."""
        for clave in self._almacen.listar():
            if clave.key_id == key_id:
                revocada = replace(clave, revocada=True)
                self._almacen.guardar(revocada)
                return revocada
        raise ClaveInvalida("No existe una access key con ese identificador.")

    def listar(self, tenant_id: str | None = None) -> list[AccessKey]:
        """Las keys (de un tenant o de todos), de la más nueva a la más vieja."""
        claves = self._almacen.listar()
        if tenant_id is not None:
            claves = [c for c in claves if c.tenant_id == tenant_id]
        return sorted(claves, key=lambda c: c.creada_en, reverse=True)

    def _nuevo_id(self) -> str:
        existentes = {c.key_id for c in self._almacen.listar()}
        while True:
            candidato = secrets.token_hex(4)
            if candidato not in existentes:
                return candidato


def _validar_etiqueta(etiqueta: str) -> str:
    etiqueta = (etiqueta or "").strip()
    if not etiqueta:
        raise DatosInvalidos("La etiqueta no puede estar vacía.")
    if len(etiqueta) > LARGO_MAXIMO_ETIQUETA:
        raise DatosInvalidos(f"La etiqueta no puede superar {LARGO_MAXIMO_ETIQUETA} caracteres.")
    if any(ord(caracter) < 32 for caracter in etiqueta):
        raise DatosInvalidos("La etiqueta no puede tener caracteres de control.")
    return etiqueta
