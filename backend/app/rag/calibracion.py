"""Calibración de los umbrales y del híbrido de un tenant a partir de preguntas típicas (ADR 0009 y 0011).

Es la misma idea con la que se calibró el retrieval léxico de digital-zur y ariel-zamora: un conjunto de preguntas
"en tema" (con el chunk que debería responderlas) y otro de preguntas "fuera de tema" (que el bot debe rechazar), y se
mide dónde cae cada una. Con eso se eligen, POR MODO (cada uno tiene su escala):

  - el umbral: el puntaje mínimo para considerar que hay una respuesta;
  - para el híbrido, además, el piso y el techo de cada señal y el peso del semántico.

Este módulo es lógica pura (numpy): recibe los puntajes ya calculados, así se prueba sin Azure. Lo usa el script
`python -m scripts.calibrar_retrieval`, que es quien calcula los puntajes con el índice y el modelo reales.

Formato del archivo de preguntas (`calibracion.json`, en la carpeta de contenido del tenant):

    {
      "en_tema": [ { "pregunta": "¿Tienen WhatsApp?", "esperado": "## Contacto" }, ... ],
      "fuera_de_tema": [ "¿Cuál es la capital de Mongolia?", ... ]
    }

`esperado` es el encabezado exacto del chunk que debería responder (la primera línea, con sus "##").
"""

import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from app.chunk_format import extract_header
from app.rag.hibrido import combinar
from app.tenants import PESO_SEMANTICO_POR_DEFECTO, ConfigHibrido

# Pesos del semántico que se prueban en el híbrido (0 = solo léxico, 1 = solo semántico).
PESOS_A_PROBAR = tuple(round(paso / 10, 1) for paso in range(0, 11))
# Un techo nunca queda pegado al piso: con una diferencia tan chica la normalización explotaría.
SEPARACION_MINIMA = 0.05
DECIMALES = 3


class CalibracionError(ValueError):
    """El archivo de preguntas está mal armado o no se puede calibrar con lo que tiene."""


@dataclass(frozen=True)
class PreguntaEnTema:
    texto: str
    esperado: str  # encabezado del chunk que debería responderla


@dataclass(frozen=True)
class PreguntasCalibracion:
    en_tema: tuple[PreguntaEnTema, ...]
    fuera_de_tema: tuple[str, ...]

    @property
    def todas(self) -> list[str]:
        """Todos los textos, en el orden en que se esperan los puntajes: primero en tema y después fuera de tema."""
        return [p.texto for p in self.en_tema] + list(self.fuera_de_tema)


def cargar_preguntas(ruta: Path) -> PreguntasCalibracion:
    """Lee y valida `calibracion.json`. Lanza CalibracionError con un mensaje claro si algo no cuadra."""
    try:
        datos = json.loads(ruta.read_text(encoding="utf-8"))
    except OSError as error:
        raise CalibracionError(f"No se pudo leer {ruta}: {type(error).__name__}.") from error
    except json.JSONDecodeError as error:
        raise CalibracionError(f"{ruta} no es un JSON válido: {error.msg} (línea {error.lineno}).") from error

    if not isinstance(datos, dict):
        raise CalibracionError(f"{ruta}: debe ser un objeto con 'en_tema' y 'fuera_de_tema'.")

    en_tema = []
    for posicion, item in enumerate(datos.get("en_tema", []), start=1):
        pregunta = item.get("pregunta") if isinstance(item, dict) else None
        esperado = item.get("esperado") if isinstance(item, dict) else None
        if not isinstance(pregunta, str) or not pregunta.strip() or not isinstance(esperado, str) or not esperado.strip():
            raise CalibracionError(f"{ruta}: la pregunta en_tema #{posicion} necesita 'pregunta' y 'esperado' (texto).")
        en_tema.append(PreguntaEnTema(pregunta.strip(), esperado.strip()))

    fuera = datos.get("fuera_de_tema", [])
    if not isinstance(fuera, list) or not all(isinstance(p, str) and p.strip() for p in fuera):
        raise CalibracionError(f"{ruta}: 'fuera_de_tema' debe ser una lista de preguntas (texto).")

    if not en_tema or not fuera:
        raise CalibracionError(
            f"{ruta}: hacen falta preguntas 'en_tema' Y 'fuera_de_tema': sin las dos no hay dónde poner el umbral."
        )
    return PreguntasCalibracion(tuple(en_tema), tuple(p.strip() for p in fuera))


def indices_esperados(corpus: pd.DataFrame, esperado: str) -> set[int]:
    """Las filas del corpus cuyo encabezado es `esperado`. Lanza CalibracionError si ninguna (suele ser un typo)."""

    def limpio(texto: str) -> str:
        return texto.strip().lstrip("#").strip().lower()

    filas = {
        posicion for posicion, texto in enumerate(corpus["text"]) if limpio(extract_header(texto)) == limpio(esperado)
    }
    if not filas:
        raise CalibracionError(f"No hay ningún chunk con el encabezado {esperado!r}: revisá que esté escrito igual.")
    return filas


# --- Resultados ---------------------------------------------------------------------------------------


@dataclass(frozen=True)
class ResultadoModo:
    """Cómo le fue a un modo con las preguntas de calibración, usando el umbral sugerido."""

    umbral: float
    aciertos: int  # preguntas en tema cuyo chunk top-1 es el esperado
    total_en_tema: int
    falsos_rechazos: int  # en tema, acertó el chunk, pero el puntaje quedó bajo el umbral (el bot diría "no sé")
    falsos_aceptos: int  # fuera de tema cuyo puntaje igual supera el umbral (el bot intentaría responder)
    total_fuera: int

    @property
    def recall_at_1(self) -> float:
        return self.aciertos / self.total_en_tema


@dataclass(frozen=True)
class Error:
    """Una pregunta en tema cuyo top-1 no fue el chunk esperado (para entender qué confunde cada modo)."""

    pregunta: str
    esperado: str
    obtenido: str
    puntaje: float


@dataclass(frozen=True)
class Calibracion:
    lexico: ResultadoModo
    semantico: ResultadoModo
    hibrido: ResultadoModo
    config_hibrido: ConfigHibrido
    por_peso: tuple[tuple[float, ResultadoModo], ...]  # el híbrido con cada peso probado
    errores: dict[str, tuple[Error, ...]]  # por modo ("lexico", "semantico", "hibrido")

    def bloque_tenant_json(self) -> dict:
        """El bloque `retrieval` listo para pegar en tenant.json. NO incluye el umbral léxico: ese ya está calibrado."""
        c = self.config_hibrido
        return {
            "retrieval": {
                "semantico": {"umbral": self.semantico.umbral},
                "hibrido": {
                    "umbral": c.umbral,
                    "peso_semantico": c.peso_semantico,
                    "piso_lexico": c.piso_lexico,
                    "techo_lexico": c.techo_lexico,
                    "piso_semantico": c.piso_semantico,
                    "techo_semantico": c.techo_semantico,
                },
            }
        }


# --- Cálculo ------------------------------------------------------------------------------------------------


def _redondear(valor: float) -> float:
    return round(float(valor), DECIMALES)


def _metrica(umbral: float, aciertos: np.ndarray, maximos: np.ndarray, en_tema: int) -> int:
    """Cuántas preguntas salen bien con este umbral: en tema acertadas y aceptadas + fuera de tema rechazadas."""
    bien_en_tema = int(np.sum(aciertos & (maximos[:en_tema] >= umbral)))
    bien_fuera = int(np.sum(maximos[en_tema:] < umbral))
    return bien_en_tema + bien_fuera


def sugerir_umbral(aciertos: np.ndarray, maximos: np.ndarray) -> float:
    """El umbral que mejor separa lo que el bot debe responder de lo que debe rechazar.

    `maximos`: el mejor puntaje de cada pregunta (primero las en tema, después las fuera de tema). `aciertos`: para
    cada pregunta en tema, si su top-1 fue el chunk esperado. Prueba los puntos medios entre puntajes consecutivos,
    se queda con los que maximizan las preguntas bien resueltas y elige el del medio de ese tramo.
    """
    en_tema = len(aciertos)
    unicos = np.unique(np.round(maximos, DECIMALES))
    candidatos = [_redondear(unicos[0] - SEPARACION_MINIMA / 5)]  # todo se acepta
    candidatos += [_redondear((a + b) / 2) for a, b in zip(unicos[:-1], unicos[1:], strict=True)]
    candidatos.append(_redondear(unicos[-1] + SEPARACION_MINIMA / 5))  # todo se rechaza

    puntos = [_metrica(c, aciertos, maximos, en_tema) for c in candidatos]
    mejores = [c for c, p in zip(candidatos, puntos, strict=True) if p == max(puntos)]
    return _redondear(float(np.clip(mejores[len(mejores) // 2], 0.0, 1.0)))


def _evaluar(
    puntajes: list[np.ndarray], esperados: list[set[int]], total_fuera: int
) -> tuple[ResultadoModo, np.ndarray, np.ndarray]:
    """Evalúa un modo: devuelve su resultado con el umbral sugerido, los aciertos y los puntajes máximos."""
    en_tema = len(esperados)
    top1 = [int(np.argmax(p)) for p in puntajes]
    maximos = np.array([float(np.max(p)) for p in puntajes])
    aciertos = np.array([top1[i] in esperados[i] for i in range(en_tema)])

    umbral = sugerir_umbral(aciertos, maximos)
    resultado = ResultadoModo(
        umbral=umbral,
        aciertos=int(aciertos.sum()),
        total_en_tema=en_tema,
        falsos_rechazos=int(np.sum(aciertos & (maximos[:en_tema] < umbral))),
        falsos_aceptos=int(np.sum(maximos[en_tema:] >= umbral)),
        total_fuera=total_fuera,
    )
    return resultado, aciertos, maximos


def sugerir_limites(
    puntajes: list[np.ndarray], esperados: list[set[int]], en_tema: int
) -> tuple[float, float]:
    """Piso y techo de una señal para normalizarla a [0, 1] en el híbrido.

    - piso: el MEJOR puntaje que sacó una pregunta fuera de tema (hasta ahí es ruido: debe normalizarse a 0).
    - techo: el puntaje típico (mediana) del chunk correcto en las preguntas en tema (eso ya es "muy relacionado": 1).
    """
    piso = max(float(np.max(p)) for p in puntajes[en_tema:])
    del_esperado = [max(float(p[i]) for i in esperados[n]) for n, p in enumerate(puntajes[:en_tema])]
    techo = float(np.median(del_esperado))
    if techo < piso + SEPARACION_MINIMA:
        techo = max(max(del_esperado), piso + SEPARACION_MINIMA)
    return _redondear(piso), _redondear(min(techo, 1.0))


def calibrar(
    corpus: pd.DataFrame,
    preguntas: PreguntasCalibracion,
    lexicos: list[np.ndarray],
    semanticos: list[np.ndarray],
) -> Calibracion:
    """Calibra los tres modos. `lexicos` y `semanticos`: el puntaje de cada chunk para cada pregunta de `preguntas.todas`."""
    en_tema = len(preguntas.en_tema)
    total = en_tema + len(preguntas.fuera_de_tema)
    if len(lexicos) != total or len(semanticos) != total:
        raise CalibracionError("Hace falta un conjunto de puntajes por cada pregunta.")
    esperados = [indices_esperados(corpus, p.esperado) for p in preguntas.en_tema]
    total_fuera = len(preguntas.fuera_de_tema)

    lexico, _, _ = _evaluar(lexicos, esperados, total_fuera)
    semantico, _, _ = _evaluar(semanticos, esperados, total_fuera)

    piso_lex, techo_lex = sugerir_limites(lexicos, esperados, en_tema)
    piso_sem, techo_sem = sugerir_limites(semanticos, esperados, en_tema)

    por_peso: list[tuple[float, ResultadoModo]] = []
    configs: dict[float, ConfigHibrido] = {}
    hibridos: dict[float, list[np.ndarray]] = {}
    for peso in PESOS_A_PROBAR:
        base = ConfigHibrido(0.0, peso, piso_lex, techo_lex, piso_sem, techo_sem)
        puntajes = [combinar(lexicos[i], semanticos[i], base) for i in range(total)]
        resultado, _, _ = _evaluar(puntajes, esperados, total_fuera)
        por_peso.append((peso, resultado))
        configs[peso] = ConfigHibrido(resultado.umbral, peso, piso_lex, techo_lex, piso_sem, techo_sem)
        hibridos[peso] = puntajes

    # El mejor peso: más aciertos de top-1; después, menos errores de umbral; y a igualdad, el más cercano al default.
    def clave(item: tuple[float, ResultadoModo]) -> tuple:
        peso, r = item
        return (-r.aciertos, r.falsos_rechazos + r.falsos_aceptos, abs(peso - PESO_SEMANTICO_POR_DEFECTO))

    mejor_peso, hibrido = min(por_peso, key=clave)

    return Calibracion(
        lexico=lexico,
        semantico=semantico,
        hibrido=hibrido,
        config_hibrido=configs[mejor_peso],
        por_peso=tuple(por_peso),
        errores={
            "lexico": _errores(corpus, preguntas, lexicos, esperados),
            "semantico": _errores(corpus, preguntas, semanticos, esperados),
            "hibrido": _errores(corpus, preguntas, hibridos[mejor_peso], esperados),
        },
    )


def _errores(
    corpus: pd.DataFrame, preguntas: PreguntasCalibracion, puntajes: list[np.ndarray], esperados: list[set[int]]
) -> tuple[Error, ...]:
    errores = []
    for n, pregunta in enumerate(preguntas.en_tema):
        top1 = int(np.argmax(puntajes[n]))
        if top1 not in esperados[n]:
            obtenido = extract_header(corpus.iloc[top1]["text"])
            errores.append(Error(pregunta.texto, pregunta.esperado, obtenido, _redondear(float(puntajes[n][top1]))))
    return tuple(errores)


# --- Reporte ------------------------------------------------------------------------------------------------


def reporte(calibracion: Calibracion) -> str:
    """El resultado en texto para leer en la consola."""
    lineas = ["Modo        top-1 correcto   umbral sugerido   falsos rechazos   falsos aceptos"]
    for nombre, r in (("léxico", calibracion.lexico), ("semántico", calibracion.semantico), ("híbrido", calibracion.hibrido)):
        lineas.append(
            f"{nombre:<11} {r.aciertos:>3}/{r.total_en_tema:<3} ({r.recall_at_1:>4.0%})   {r.umbral:>8.3f}"
            f"          {r.falsos_rechazos:>3}/{r.total_en_tema:<3}           {r.falsos_aceptos:>3}/{r.total_fuera:<3}"
        )
    c = calibracion.config_hibrido
    lineas += [
        "",
        f"Híbrido elegido: peso del semántico {c.peso_semantico}, léxico [{c.piso_lexico}, {c.techo_lexico}], "
        f"semántico [{c.piso_semantico}, {c.techo_semantico}]",
        "Peso del semántico -> top-1 correcto / errores de umbral: "
        + "  ".join(f"{p}: {r.aciertos}/{r.falsos_rechazos + r.falsos_aceptos}" for p, r in calibracion.por_peso),
    ]
    for modo, errores in calibracion.errores.items():
        if errores:
            lineas += ["", f"Preguntas que el modo {modo} responde con otro chunk ({len(errores)}):"]
            lineas += [f"  - {e.pregunta!r}: esperaba {e.esperado!r}, trajo {e.obtenido!r} ({e.puntaje})" for e in errores[:10]]
    return "\n".join(lineas)
