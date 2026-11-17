"""Calibra los umbrales del retrieval semántico e híbrido de un tenant con preguntas típicas (ADR 0009 y 0011).

Correr desde backend/ con el venv activado:

    python -m scripts.calibrar_retrieval --tenant digital-zur

Lee las preguntas de `<carpeta de contenido del tenant>/calibracion.json` (o de --preguntas), calcula los puntajes léxico
y semántico de cada una con el contenido REAL del tenant y el modelo de embeddings de Foundry, y muestra:
  - cómo le va a cada modo (léxico, semántico, híbrido) y qué umbral separa mejor lo que debe responder de lo que no;
  - un bloque `retrieval` para pegar en backend/tenants/<tenant>/tenant.json.

Cuesta tokens de embeddings: los chunks que aún no estén guardados y las preguntas (unas decenas de tokens cada una:
centavos de dólar o menos). Los vectores de los chunks se guardan en `<carpeta del tenant>/.vectores/`, igual que cuando
atiende el chat, así que una segunda corrida solo paga las preguntas. No toca ningún archivo de configuración: el
resultado lo pegás vos. Necesita `az login` y FOUNDRY_EMBEDDINGS_DEPLOYMENT en backend/.env.

Para probar el script SIN Azure: agregá --fake. Usa un "modelo" falso (bolsa de palabras): los números no sirven para
calibrar, solo comprueban que el archivo de preguntas y el cálculo funcionan. No guarda vectores.
"""

import argparse
import json
import re
import sys
import zlib
from collections.abc import Sequence
from pathlib import Path

import numpy as np

from app.foundry.embeddings import FoundryEmbeddings
from app.foundry.generador import FoundryGenerador
from app.foundry.tipos import FoundryError, GeneradorEmbeddings, ResultadoEmbeddings
from app.rag.almacen_vectores import NOMBRE_CARPETA, AlmacenVectores, construir_vectores
from app.rag.calibracion import CalibracionError, calibrar, cargar_preguntas, reporte
from app.rag.retrieve import puntajes_lexicos
from app.rag.semantico import normalizar, similitudes
from app.reindex import carpeta_de_contenido, reindex
from app.tenants import TenantInvalido, TenantNoEncontrado, obtener_tenant

ARCHIVO_PREGUNTAS = "calibracion.json"


class EmbeddingsDePrueba:
    """Un "modelo" falso para --fake: vectoriza por bolsa de palabras. Sin Azure, sin costo, sin significado real."""

    DIMENSIONES = 4096

    def deployment(self) -> str:
        return "embeddings-de-prueba"

    def embeber(self, textos: Sequence[str]) -> ResultadoEmbeddings:
        vectores = np.zeros((len(textos), self.DIMENSIONES), dtype=np.float32)
        for fila, texto in enumerate(textos):
            for palabra in re.findall(r"\w+", texto.lower()):
                vectores[fila, zlib.crc32(palabra.encode("utf-8")) % self.DIMENSIONES] += 1.0
        return ResultadoEmbeddings(vectores, 0, self.deployment(), 0.0)


def main(argv: Sequence[str] | None = None, embeddings: GeneradorEmbeddings | None = None) -> int:
    """Corre la calibración. `embeddings` permite inyectar un generador (los tests le pasan uno falso)."""
    parser = argparse.ArgumentParser(description="Calibra los umbrales del retrieval semántico e híbrido de un tenant.")
    parser.add_argument("--tenant", required=True, help="id del tenant, por ejemplo digital-zur")
    parser.add_argument("--preguntas", type=Path, help=f"archivo de preguntas (por defecto, <contenido del tenant>/{ARCHIVO_PREGUNTAS})")
    parser.add_argument("--fake", action="store_true", help="usa un modelo falso: prueba el script sin Azure (no calibra)")
    args = parser.parse_args(argv)

    try:
        tenant = obtener_tenant(args.tenant)
        carpeta = carpeta_de_contenido(tenant)
        preguntas = cargar_preguntas(args.preguntas or carpeta / ARCHIVO_PREGUNTAS)

        indice = reindex(tenant.tenant_id)
        if indice.corpus.empty:
            raise CalibracionError(f"El tenant '{tenant.tenant_id}' no tiene contenido para calibrar.")

        if embeddings is None:
            embeddings = EmbeddingsDePrueba() if args.fake else FoundryEmbeddings(FoundryGenerador().obtener_cliente_embeddings)
        # Con --fake no se guarda nada: vectores falsos en la carpeta del tenant contaminarían el chat.
        solo_memoria = args.fake or tenant.usa_sample
        almacen = AlmacenVectores(None if solo_memoria else carpeta / NOMBRE_CARPETA)

        chunks = construir_vectores(indice.corpus, embeddings, almacen)
        # Todas las preguntas en UNA llamada (en lotes), no una por pregunta.
        de_preguntas = embeddings.embeber(preguntas.todas)
        consultas = normalizar(de_preguntas.vectores)

        semanticos = [similitudes(consulta, chunks.matriz) for consulta in consultas]
        lexicos = [puntajes_lexicos(texto, indice.vectorizer, indice.matrix) for texto in preguntas.todas]
        calibracion = calibrar(indice.corpus, preguntas, lexicos, semanticos)
    except (CalibracionError, TenantInvalido, TenantNoEncontrado, FoundryError) as error:
        print(f"No se pudo calibrar: {error}", file=sys.stderr)
        return 1

    print(f"Tenant: {tenant.tenant_id} | {len(indice.corpus)} chunks | {len(preguntas.en_tema)} en tema, "
          f"{len(preguntas.fuera_de_tema)} fuera de tema")
    print(f"Tokens de embeddings de esta corrida: {chunks.tokens + de_preguntas.tokens} "
          f"(chunks nuevos: {chunks.nuevos}, reutilizados: {chunks.reutilizados})\n")
    print(reporte(calibracion))
    print(f"\nUmbral léxico actual en tenant.json: {tenant.umbral_similitud} (el sugerido arriba es solo de referencia).")
    print("\nPegá esto en backend/tenants/<tenant>/tenant.json (junto a los demás campos):\n")
    print(json.dumps(calibracion.bloque_tenant_json(), indent=2, ensure_ascii=False))
    print('\nPara que el tenant use un modo por defecto (el widget público no elige), sumá dentro de "retrieval":')
    print('  "por_defecto": "hibrido"   (o "semantico"). Sin ese campo sigue respondiendo con el léxico.')
    if args.fake:
        print("\nATENCIÓN: corrida con --fake, estos números no sirven para calibrar.")
    return 0


# Solo corre main() si ejecutás este archivo directamente (no si lo importan).
if __name__ == "__main__":
    sys.exit(main())
