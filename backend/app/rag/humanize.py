"""Humanización de la respuesta recuperada.

Esto es puramente cosmético: toma la respuesta cruda que ya devolvió
retrieve.py (sin tocarlo) y le agrega una apertura conversacional y una
limpieza de formato. No genera contenido nuevo ni cambia el ranking.
"""

import random
import re
import unicodedata

# Aperturas agrupadas por la palabra clave de la pregunta que las dispara.
# Cada grupo tiene varias variantes; random.choice elige una al azar cada
# vez para no sonar repetitivo si se hace la misma pregunta varias veces.
_APERTURAS_POR_PALABRA_CLAVE = {
    "como": [
        "Te cuento cómo lo encaro: ",
        "Así es como lo manejo: ",
        "Buena pregunta, va así: ",
    ],
    "cuanto": [
        "Te paso el número: ",
        "Contando todo: ",
        "Ahí va el dato: ",
    ],
    "donde": [
        "Te cuento dónde: ",
        "Ubicándote un poco: ",
    ],
    "cual": [
        "Te cuento cuál: ",
        "Ahí va: ",
    ],
    "quien": [
        "Te cuento quién: ",
        "Sobre eso: ",
    ],
    # "que" va último a propósito: es la palabra clave más genérica y
    # aparece como substring de otras (porque, aunque, etc). Si estuviera
    # primero en el dict, "matchearía" antes que palabras más específicas
    # para preguntas que también las contienen.
    "que": [
        "Te cuento: ",
        "Mirá, lo que pasa es que: ",
        "Ahí va: ",
    ],
}

# Apertura genérica para preguntas que no matchean ninguna palabra clave.
_APERTURAS_GENERICAS = [
    "Te cuento: ",
    "Mirá: ",
    "Ahí va: ",
]

# Aperturas para cuando el score de similitud es bajo: en vez de la apertura
# "normal", se sincera que la respuesta puede no ser exactamente lo que se
# buscaba (mejor esto que devolver un chunk irrelevante como si fuera obvio).
_APERTURAS_BAJA_CONFIANZA = [
    "No tengo algo que responda esto puntualmente, pero lo más parecido que encontré es: ",
    "No estoy del todo seguro de que esto sea lo que buscás, pero es lo más cercano que tengo: ",
    "Esto no me cierra del todo como respuesta, pero es lo más relacionado que encontré: ",
]

# Se bajó de 0.25 a 0.15 al filtrar stopwords en el TF-IDF: los scores de preguntas bien respondidas
# quedaron algo más bajos (una pregunta correcta del perfil ficticio da 0.249).
UMBRAL_BAJA_CONFIANZA = 0.15


def _quitar_acentos(texto: str) -> str:
    """Normaliza tildes para poder matchear "cómo" con la clave "como", etc.

    NFKD descompone cada letra acentuada en (letra base + acento) como dos
    caracteres separados; unicodedata.combining() detecta cuáles de esos
    caracteres son "el acento" para poder filtrarlos y quedarnos solo con
    las letras base.
    """
    forma_descompuesta = unicodedata.normalize("NFKD", texto)
    return "".join(c for c in forma_descompuesta if not unicodedata.combining(c))


def _elegir_apertura(pregunta: str) -> str:
    """Elige una apertura al azar según la primera palabra clave que matchee."""
    pregunta_normalizada = _quitar_acentos(pregunta.lower())
    for palabra_clave, aperturas in _APERTURAS_POR_PALABRA_CLAVE.items():
        if palabra_clave in pregunta_normalizada:
            return random.choice(aperturas)
    return random.choice(_APERTURAS_GENERICAS)


def _limpiar(texto: str) -> str:
    """Colapsa espacios repetidos, capitaliza la primera letra y cierra con punto si falta."""
    # \s+ -> " " colapsa cualquier corrida de espacios/tabs/saltos de línea
    # en un solo espacio (por si `respuesta` venía con saltos de línea del
    # chunk original).
    texto = re.sub(r"\s+", " ", texto).strip()
    if not texto:
        return texto

    texto = texto[0].upper() + texto[1:]

    if texto[-1] not in ".!?":
        texto += "."

    return texto


def humanizar(pregunta: str, respuesta: str, score: float) -> str:
    """Envuelve una respuesta cruda en un tono más conversacional.

    - Si el score es bajo (< UMBRAL_BAJA_CONFIANZA), la apertura sincera
      la baja confianza EN VEZ DE la apertura "normal" por palabra clave
      (decisión de diseño: sonaría contradictorio combinar "te cuento
      cómo lo encaro" con "no estoy seguro de esto").
    - Si el score es aceptable, se usa la apertura elegida por palabra
      clave de la pregunta.
    - En ambos casos, el texto final pasa por _limpiar().

    No combina múltiples chunks (eso queda para más adelante) — opera
    sobre una sola respuesta ya extraída.
    """
    if score < UMBRAL_BAJA_CONFIANZA:
        apertura = random.choice(_APERTURAS_BAJA_CONFIANZA)
    else:
        apertura = _elegir_apertura(pregunta)

    return _limpiar(apertura + respuesta)
