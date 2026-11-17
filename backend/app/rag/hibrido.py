"""Retrieval híbrido: combina el puntaje léxico (TF-IDF) y el semántico (embeddings) en uno solo (ADR 0011).

Cada uno ve algo distinto. El léxico acierta con palabras exactas (un nombre propio, una sigla como "AZ-104") pero no
entiende sinónimos; el semántico entiende significado pero puede diluir un término raro. Combinados se compensan.

El problema es que no se pueden sumar tal cual: el coseno de TF-IDF vale ~0 para un texto sin relación, y el de
embeddings casi nunca baja de ~0,2. Por eso, ANTES de combinar, cada puntaje se lleva a [0, 1] con un piso y un techo
propios del tenant (salen de la calibración, ver app/rag/calibracion.py):

    normalizado = clip((puntaje - piso) / (techo - piso), 0, 1)

Se usan límites FIJOS y no el mínimo y máximo de cada consulta: con estos últimos, el mejor chunk de una pregunta fuera
de tema también llegaría a 1 y ningún umbral podría decir "no tengo información".
"""

import numpy as np
import pandas as pd

from app.rag.semantico import top_k_de_puntajes
from app.tenants import ConfigHibrido


def normalizar_puntaje(puntajes: np.ndarray, piso: float, techo: float) -> np.ndarray:
    """Lleva los puntajes a [0, 1]: `piso` (o menos) da 0 y `techo` (o más) da 1. Requiere techo > piso."""
    return np.clip((np.asarray(puntajes, dtype=float) - piso) / (techo - piso), 0.0, 1.0)


def combinar(lexicos: np.ndarray, semanticos: np.ndarray, config: ConfigHibrido) -> np.ndarray:
    """Puntaje híbrido de cada chunk: peso · semántico_normalizado + (1 − peso) · léxico_normalizado, en [0, 1]."""
    lexico = normalizar_puntaje(lexicos, config.piso_lexico, config.techo_lexico)
    semantico = normalizar_puntaje(semanticos, config.piso_semantico, config.techo_semantico)
    return config.peso_semantico * semantico + (1.0 - config.peso_semantico) * lexico


def top_k_hibrido(
    lexicos: np.ndarray, semanticos: np.ndarray, config: ConfigHibrido, corpus: pd.DataFrame, k: int = 3
) -> pd.DataFrame:
    """Los k chunks con mayor puntaje híbrido (columna `similarity`, ya en la escala normalizada del híbrido)."""
    return top_k_de_puntajes(combinar(lexicos, semanticos, config), corpus, k)
