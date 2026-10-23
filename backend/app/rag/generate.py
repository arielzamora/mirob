"""Generación con LLM sobre los chunks recuperados (modo "foundry"), con corte por umbral.

Es la regla de costos de MiRob (FinOps): el LLM solo se invoca si la búsqueda local
encontró algo suficientemente parecido. Si no, se responde con un mensaje fijo y el
gasto es de 0 tokens.

Este módulo NO conoce el SDK de Azure: recibe un `GeneradorTexto` (ver
app/foundry/tipos.py), igual que un servicio de C# que recibe una interfaz por DI.
Por eso se prueba sin red, con un generador falso.
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass

import pandas as pd

from app.foundry.tipos import MAX_TOKENS_SALIDA, GeneradorTexto
from app.tenants import TenantConfig

# Mensaje fijo cuando no hay nada suficientemente parecido. Se usa tal cual: no pasa por el LLM.
MENSAJE_SIN_INFORMACION = "No encontré información sobre esa consulta en la base de datos de {nombre}."

# Mensaje fijo cuando el modelo respondió pero no devolvió texto (por ejemplo, un modelo de
# razonamiento que gastó todo el tope de salida pensando). Es distinto del anterior a propósito:
# aquí SÍ había información, pero no se pudo redactar la respuesta.
MENSAJE_RESPUESTA_VACIA = "No pude redactar una respuesta en este momento. Probá reformular la pregunta."

# Máximo de chunks que viajan al LLM como contexto (más chunks = más tokens de entrada).
MAX_CHUNKS_CONTEXTO = 3

# Tope de caracteres por chunk que entra al prompt, para acotar los tokens de entrada
# aunque alguien suba un chunk enorme.
MAX_CARACTERES_CHUNK = 1500

# Historial que entra al prompt: los últimos 6 mensajes (3 idas y vueltas), cada uno
# recortado. El historial es contexto de conversación, NO se usa para buscar.
MAX_MENSAJES_HISTORIAL_PROMPT = 6
MAX_CARACTERES_MENSAJE_HISTORIAL_PROMPT = 500

# Etiquetas <contexto>...</contexto> que usamos como delimitador del prompt. Si un chunk,
# el historial o la pregunta las trajeran adentro, podrían "cerrar" el bloque y colar
# instrucciones: se eliminan antes de armar el prompt.
_ETIQUETAS_DELIMITADOR = re.compile(r"</?\s*contexto\s*>", re.IGNORECASE)


@dataclass(frozen=True)
class Turno:
    """Un mensaje previo de la conversación (role: "user" o "assistant")."""

    role: str
    content: str


@dataclass(frozen=True)
class RespuestaGenerada:
    """Resultado del modo foundry: el texto y cuánto costó producirlo."""

    texto: str
    llm_invocado: bool
    tokens_entrada: int = 0
    tokens_salida: int = 0
    tokens_total: int = 0
    latencia_ms: float | None = None
    deployment: str | None = None


def _neutralizar(texto: str) -> str:
    """Quita las etiquetas delimitadoras del texto, para que sea solo datos."""
    return _ETIQUETAS_DELIMITADOR.sub("", texto)


def _acortar(texto: str, maximo: int) -> str:
    """Corta el texto a `maximo` caracteres, marcando el corte con '…'."""
    return texto if len(texto) <= maximo else texto[: maximo - 1].rstrip() + "…"


def recortar_historial(historial: Sequence[Turno] | None) -> list[Turno]:
    """Se queda con los últimos mensajes y acorta cada uno."""
    if not historial:
        return []
    # [-N:] toma los últimos N elementos de la lista (los más recientes).
    ultimos = list(historial)[-MAX_MENSAJES_HISTORIAL_PROMPT:]
    return [Turno(t.role, _acortar(t.content.strip(), MAX_CARACTERES_MENSAJE_HISTORIAL_PROMPT)) for t in ultimos]


def seleccionar_chunks_contexto(resultado: pd.DataFrame, umbral: float) -> pd.DataFrame:
    """Los chunks que van al LLM: solo los que superan el umbral, hasta MAX_CHUNKS_CONTEXTO.

    `resultado` es la salida de top_k (ordenada por similitud descendente). Quedarse solo
    con los que pasan el umbral evita pagar tokens por chunks que no vienen al caso.
    """
    return resultado[resultado["similarity"] >= umbral].head(MAX_CHUNKS_CONTEXTO)


def construir_mensaje(pregunta: str, chunks: pd.DataFrame, historial: Sequence[Turno] | None) -> str:
    """Arma el mensaje de usuario: contexto delimitado + historial + pregunta.

    Los fragmentos van entre <contexto> y </contexto> y el system prompt del tenant
    indica tratarlos como datos, no como instrucciones (defensa básica contra prompt
    injection: ver Parte 2.5 del plan para las salvaguardas completas).
    """
    fragmentos = []
    for posicion, (_, fila) in enumerate(chunks.iterrows(), start=1):
        texto = _acortar(_neutralizar(fila["text"]), MAX_CARACTERES_CHUNK)
        fragmentos.append(f"[{posicion}] (fuente: {fila['source']})\n{texto}")

    partes = ["<contexto>\n" + "\n\n".join(fragmentos) + "\n</contexto>"]

    turnos = recortar_historial(historial)
    if turnos:
        etiquetas = {"user": "Usuario", "assistant": "Asistente"}
        lineas = [f"{etiquetas.get(t.role, t.role)}: {_neutralizar(t.content)}" for t in turnos]
        partes.append("Conversación previa:\n" + "\n".join(lineas))

    partes.append(f"Pregunta: {_neutralizar(pregunta)}")
    return "\n\n".join(partes)


def generar_respuesta(
    tenant: TenantConfig,
    pregunta: str,
    resultado: pd.DataFrame,
    historial: Sequence[Turno] | None,
    generador: GeneradorTexto,
) -> RespuestaGenerada:
    """Aplica el umbral y, solo si corresponde, llama al LLM.

    - Ningún chunk supera el umbral del tenant → mensaje fijo, 0 tokens, NO se llama al generador.
    - Hay chunks → se arma el prompt y se llama al generador con el system prompt del tenant,
      limitando la salida a MAX_TOKENS_SALIDA.
    Puede lanzar FoundryError (lo traduce la capa HTTP).
    """
    sin_informacion = MENSAJE_SIN_INFORMACION.format(nombre=tenant.nombre)

    chunks = seleccionar_chunks_contexto(resultado, tenant.umbral_similitud)
    if chunks.empty:
        return RespuestaGenerada(texto=sin_informacion, llm_invocado=False)

    mensaje = construir_mensaje(pregunta, chunks, historial)
    resultado_llm = generador.generar(tenant.system_prompt, mensaje, MAX_TOKENS_SALIDA)

    return RespuestaGenerada(
        # Si el modelo devolvió vacío (p. ej. se cortó), mejor un mensaje fijo que una burbuja en blanco.
        texto=resultado_llm.texto.strip() or MENSAJE_RESPUESTA_VACIA,
        llm_invocado=True,
        tokens_entrada=resultado_llm.tokens_entrada,
        tokens_salida=resultado_llm.tokens_salida,
        tokens_total=resultado_llm.tokens_total,
        latencia_ms=resultado_llm.latencia_ms,
        deployment=resultado_llm.deployment,
    )
