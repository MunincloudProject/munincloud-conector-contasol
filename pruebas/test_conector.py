"""Pruebas del conector Contasol. Solo biblioteca estándar:

    python3 -m unittest discover -s pruebas -v

Plantilla del kit de conectores de MuninCloud. Copia como `pruebas/test_conector.py`, cambia `CONECTOR` y `SALIDAS`,
y añade las pruebas propias del programa al final. Las tres de arriba son las mínimas de todo conector.
"""
import contextlib
import csv
import hashlib
import io
import os
import shutil
import sys
import tempfile
import unittest

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)
import munincloud_v1 as v1  # noqa: E402

CONECTOR = "munincloud_contasol"           # el módulo del conector, sin .py
SALIDAS = ("omitidos.csv",)                 # los ficheros de ejemplos/salida/ que se comparan por md5
REVISAR = ("revisar.csv", "documento", None)  # fichero de revisar, columna del documento y filtro (None, o
                                            # ("revisar", "SI") si el fichero lleva todas y marca las de revisar)
ENTRADA = os.path.join(RAIZ, "ejemplos", "entrada")
SALIDA_REF = os.path.join(RAIZ, "ejemplos", "salida")
DATOS = os.path.join(RAIZ, "pruebas", "datos")
MI_NIF = "B00000018"   # NIF reservado por la AEAT para pruebas

conector = __import__(CONECTOR)
_TEMPORALES = []


def tearDownModule():
    for d in _TEMPORALES:
        shutil.rmtree(d, ignore_errors=True)


def generar(entradas, *extra):
    salida = tempfile.mkdtemp(prefix=CONECTOR + "_")
    _TEMPORALES.append(salida)
    with contextlib.redirect_stdout(io.StringIO()):
        conector.main(["--mi-nif", MI_NIF, "--salida", salida, *extra, *entradas])
    return salida


def documentos(ruta, columna, solo_si=None):
    if not os.path.exists(ruta):
        return set()
    with open(ruta, encoding="utf-8-sig", newline="") as fh:
        return {f.get(columna, "") for f in csv.DictReader(fh, delimiter=";")
                if solo_si is None or f.get(solo_si[0]) == solo_si[1]}


def md5(ruta):
    with open(ruta, "rb") as fh:
        return hashlib.md5(fh.read()).hexdigest()


class Kit(unittest.TestCase):
    """Las mínimas del kit: la salida es la del ejemplo, lo desafinado se aparta, lo que la pieza aparta no se pierde."""

    def test_salida_igual_a_la_del_ejemplo(self):
        salida = generar([ENTRADA])
        for nombre in SALIDAS:
            self.assertEqual(md5(os.path.join(salida, nombre)), md5(os.path.join(SALIDA_REF, nombre)), nombre)

    def test_lo_desafinado_sale_a_revisar(self):
        for nombre in sorted(os.listdir(DATOS)):
            if not nombre.startswith("desafinada__"):
                continue
            salida = generar([os.path.join(DATOS, nombre)])
            self.assertIn(nombre, documentos(os.path.join(salida, REVISAR[0]), REVISAR[1], REVISAR[2]), nombre)

    def test_lo_que_la_pieza_aparta_sale_a_revisar(self):
        salida = generar([ENTRADA])
        apartados = documentos(os.path.join(salida, REVISAR[0]), REVISAR[1], REVISAR[2])
        for e in v1.leer_entradas([ENTRADA]):
            if e.get("documento") and v1.analizar(e["documento"], MI_NIF)["revisar"]:
                self.assertIn(e["origen"], apartados, e["origen"])


if __name__ == "__main__":
    unittest.main()
