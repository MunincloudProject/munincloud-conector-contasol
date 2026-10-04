"""Pruebas propias del conector Contasol (las mínimas del kit están en test_conector.py). Solo biblioteca estándar:

    python3 -m unittest discover -s pruebas -v
"""
import contextlib
import copy
import csv
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)
sys.path.insert(0, os.path.join(RAIZ, "pruebas"))
import munincloud_contasol as conector  # noqa: E402
import via_b  # noqa: E402  (solo para LEER las hojas en las pruebas; la vía B se ejecuta aparte, por subproceso)

ENTRADA = os.path.join(RAIZ, "ejemplos", "entrada")
SALIDA_REF = os.path.join(RAIZ, "ejemplos", "salida")
MAESTRO = os.path.join(RAIZ, "ejemplos", "proveedores.csv")
VIA_B = os.path.join(RAIZ, "pruebas", "via_b.py")
MI_NIF = "B00000018"
# Los parámetros de los ejemplos: los mismos que el README («En cinco minutos») y conector.json.
PARAMETROS = ["--maestro", MAESTRO, "--libro-iva", "1", "--diario", "1", "--cuenta-iva", "472",
              "--cuenta-gasto", "629", "--cuenta-retencion", "4751"]
_TEMPORALES = []


def tearDownModule():
    for d in _TEMPORALES:
        shutil.rmtree(d, ignore_errors=True)


def temporal():
    d = tempfile.mkdtemp(prefix="munincloud_contasol_")
    _TEMPORALES.append(d)
    return d


def generar(entradas, *extra, parametros=True):
    salida = temporal()
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        rc = conector.main(["--mi-nif", MI_NIF, "--salida", salida, *(PARAMETROS if parametros else []), *extra,
                            *entradas])
    return salida, rc


def caso(nombre, cambiar):
    """Una respuesta de los ejemplos, cambiada por `cambiar(documento)`, en un .json temporal."""
    with open(os.path.join(ENTRADA, nombre), encoding="utf-8") as fh:
        obj = copy.deepcopy(json.load(fh))
    cambiar(obj["documento"])
    ruta = os.path.join(temporal(), "caso__" + nombre)
    with open(ruta, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False)
    return ruta


def hoja(salida, nombre):
    ruta = os.path.join(salida, f"{nombre}.ods")
    return [{k: v[2] for k, v in f.items()} for f in via_b.leer_ods(ruta, nombre)] if os.path.exists(ruta) else None


def filas_csv(ruta):
    with open(ruta, encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh, delimiter=";"))


def motivos(salida):
    return {f["documento"]: f["motivos"] for f in filas_csv(os.path.join(salida, "revisar.csv"))}


def md5(ruta):
    with open(ruta, "rb") as fh:
        return hashlib.md5(fh.read()).hexdigest()


def correr_via_b(salida, entradas=ENTRADA):
    return subprocess.run([sys.executable, VIA_B, salida, entradas], capture_output=True, text=True, timeout=120)


def reescribir_ods(ruta, cambiar):
    """Reescribe content.xml con `cambiar(texto)` conservando el orden del ZIP (para las contrapruebas)."""
    with zipfile.ZipFile(ruta) as z:
        partes = [(i, z.read(i.filename)) for i in z.infolist()]
    with zipfile.ZipFile(ruta, "w") as z:
        for info, datos in partes:
            if info.filename == "content.xml":
                datos = cambiar(datos.decode("utf-8")).encode("utf-8")
            z.writestr(info, datos)


class Ejemplos(unittest.TestCase):

    def test_toda_la_salida_igual_a_la_del_ejemplo(self):
        salida, rc = generar([ENTRADA])
        self.assertEqual(rc, 0)
        self.assertEqual(sorted(os.listdir(salida)), sorted(os.listdir(SALIDA_REF)))
        for nombre in os.listdir(SALIDA_REF):
            self.assertEqual(md5(os.path.join(salida, nombre)), md5(os.path.join(SALIDA_REF, nombre)), nombre)

    def test_mimetype_primero_y_sin_comprimir(self):
        for nombre in ("IVS.ods", "APU.ods"):
            with zipfile.ZipFile(os.path.join(SALIDA_REF, nombre)) as z:
                primera = z.infolist()[0]
                self.assertEqual(primera.filename, "mimetype")
                self.assertEqual(primera.compress_type, zipfile.ZIP_STORED)
                self.assertEqual(z.read("mimetype"), b"application/vnd.oasis.opendocument.spreadsheet")

    def test_via_b_da_ok_sobre_los_ejemplos(self):
        r = correr_via_b(SALIDA_REF)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("VIA_B=OK", r.stdout)


class Contrapruebas(unittest.TestCase):
    """C10: la vía B tiene que ponerse en rojo con lo que SÍ tiene que pegar."""

    def copia(self):
        d = temporal()
        for n in os.listdir(SALIDA_REF):
            shutil.copy(os.path.join(SALIDA_REF, n), d)
        return d

    def test_rojo_si_se_cambia_un_importe(self):
        d = self.copia()
        reescribir_ods(os.path.join(d, "IVS.ods"), lambda t: t.replace(
            'office:value="181.50"><text:p>181,50</text:p>', 'office:value="181.60"><text:p>181,60</text:p>', 1))
        self.assertNotEqual(md5(os.path.join(d, "IVS.ods")), md5(os.path.join(SALIDA_REF, "IVS.ods")))
        self.assertEqual(correr_via_b(d).returncode, 1)

    def test_rojo_si_se_borra_una_fila_del_ivs(self):
        d = self.copia()
        reescribir_ods(os.path.join(d, "IVS.ods"), lambda t: t.replace(
            t[t.index("<table:table-row>"):t.index("</table:table-row>") + len("</table:table-row>")], "", 1))
        self.assertEqual(correr_via_b(d).returncode, 1)

    def test_rojo_si_se_borra_un_apunte(self):
        d = self.copia()
        reescribir_ods(os.path.join(d, "APU.ods"), lambda t: t.replace(
            t[t.index("<table:table-row>"):t.index("</table:table-row>") + len("</table:table-row>")], "", 1))
        self.assertEqual(correr_via_b(d).returncode, 1)

    def test_rojo_si_un_texto_pasa_de_su_maximo(self):
        d = self.copia()
        reescribir_ods(os.path.join(d, "IVS.ods"), lambda t: t.replace(
            "<text:p>P-2026-0925</text:p>", "<text:p>P-2026-0925-XYZ</text:p>", 1))
        self.assertEqual(correr_via_b(d).returncode, 1)

    def test_rojo_si_mimetype_no_va_primero(self):
        d = self.copia()
        ruta = os.path.join(d, "IVS.ods")
        with zipfile.ZipFile(ruta) as z:
            partes = [(i, z.read(i.filename)) for i in z.infolist()]
        with zipfile.ZipFile(ruta, "w") as z:
            for info, datos in reversed(partes):
                z.writestr(info, datos)
        self.assertEqual(correr_via_b(d).returncode, 1)


class Reglas(unittest.TestCase):

    def test_las_emitidas_salen_a_revisar_y_no_entran_en_el_ivs(self):
        salida, _ = generar([os.path.join(ENTRADA, "ventas__identidad.json")])
        self.assertIn("CONTASOL_VENTA_SIN_LIBRO", motivos(salida)["ventas__identidad.json"])
        self.assertIsNone(hoja(salida, "IVS"))

    def test_mas_de_tres_tipos_sale_a_revisar(self):
        salida, _ = generar([os.path.join(ENTRADA, "contasol__cuatro_tipos.json")])
        self.assertIn("CONTASOL_DEMASIADOS_TIPOS", motivos(salida)["contasol__cuatro_tipos.json"])

    def test_numero_largo_se_recorta_por_la_cola_y_va_entero_en_bv(self):
        salida, _ = generar([os.path.join(ENTRADA, "contasol__numero_largo.json")])
        f = hoja(salida, "IVS")[0]
        self.assertEqual(f["E"], "RIE-A-000123")
        self.assertIn("FRA-2026-SERIE-A-000123", f["BV"])
        self.assertEqual(len(f["F"]), 100)
        codigos = [a["codigo"] for a in filas_csv(os.path.join(salida, "avisos.csv"))]
        self.assertIn("CONTASOL_NUMERO_RECORTADO", codigos)
        self.assertIn("CONTASOL_CAMPO_RECORTADO", codigos)

    def test_recargo_en_su_columna_y_en_el_asiento(self):
        salida, _ = generar([os.path.join(ENTRADA, "contasol__recargo.json")])
        f = hoja(salida, "IVS")[0]
        self.assertEqual((f["P"], f["V"], f["Y"]), ("5,2", "5,20", "126,20"))
        self.assertEqual([a["I"] for a in hoja(salida, "APU")], ["100,00", "26,20", "0,00"])

    def test_retencion_como_porcentaje_y_al_haber(self):
        salida, _ = generar([os.path.join(ENTRADA, "compras__mixta_irpf.json")])
        f = hoja(salida, "IVS")[0]
        self.assertEqual((f["AT"], f["AU"], f["BB"]), ("15", "15,00", "3"))
        apu = hoja(salida, "APU")
        self.assertEqual([(a["E"], a["J"]) for a in apu if a["J"] != "0,00"],
                         [("47510000", "15,00"), ("40000001", "161,00")])

    def test_intracomunitaria_de_bienes(self):
        salida, _ = generar([os.path.join(ENTRADA, "compras__intracomunitaria.json")])
        f = hoja(salida, "IVS")[0]
        self.assertEqual((f["G"], f["BC"], f["BS"], f["H"], f["BB"]), ("40123456824", "2", "FR", "2", "17"))

    def test_rectificativa_siempre_a_revisar(self):
        salida, _ = generar([os.path.join(ENTRADA, "compras__rectificativa.json")])
        self.assertIn("CONTASOL_RECTIFICATIVA", motivos(salida)["compras__rectificativa.json"])
        self.assertIn(hoja(salida, "IVS"), ([], None))
        self.assertIn(hoja(salida, "APU"), ([], None))

    def test_rectificativa_emitida_tambien_a_revisar(self):
        def emitida(d):
            d["proveedor"]["cif_nif"], d["cliente"]["cif_nif"] = d["cliente"]["cif_nif"], d["proveedor"]["cif_nif"]
            d["direccion"] = "EMITIDA"
        ruta = caso("compras__rectificativa.json", emitida)
        salida, _ = generar([ruta])
        m = motivos(salida)[os.path.basename(ruta)]
        self.assertIn("CONTASOL_VENTA_SIN_LIBRO", m)          # de verdad es una emitida
        self.assertIn("CONTASOL_RECTIFICATIVA", m)

    def test_no_rectificativa_sin_ese_motivo(self):
        salida, _ = generar([ENTRADA])
        con = [n for n, m in motivos(salida).items() if "CONTASOL_RECTIFICATIVA" in m]
        self.assertEqual(con, ["compras__rectificativa.json"])

    def test_sin_maestro_sale_a_revisar_y_con_cuenta_por_defecto_entra(self):
        ruta = os.path.join(ENTRADA, "compras__identidad.json")
        salida, _ = generar([ruta], parametros=False)
        self.assertIn("CONTASOL_SIN_CUENTA_PROVEEDOR", motivos(salida)["compras__identidad.json"])
        salida, _ = generar([ruta], "--cuenta-proveedor", "400", parametros=False)
        self.assertEqual(hoja(salida, "IVS")[0]["D"], "40000000")

    def test_sin_cuentas_no_hay_apu_y_se_dice(self):
        salida, _ = generar([ENTRADA], "--maestro", MAESTRO, parametros=False)
        self.assertFalse(os.path.exists(os.path.join(salida, "APU.ods")))
        self.assertTrue(os.path.exists(os.path.join(salida, "IVS.ods")))
        codigos = [a["codigo"] for a in filas_csv(os.path.join(salida, "avisos.csv"))]
        self.assertIn("CONTASOL_SIN_ASIENTO", codigos)
        self.assertIn("CONTASOL_LIBRO_IVA_SUPUESTO", codigos)
        self.assertEqual(correr_via_b(salida).returncode, 0)

    def test_irpf_sin_cuenta_de_retenciones_no_hay_apu(self):
        p = [x for x in PARAMETROS[:-2]]
        salida, _ = generar([os.path.join(ENTRADA, "compras__mixta_irpf.json")], *p, parametros=False)
        self.assertFalse(os.path.exists(os.path.join(salida, "APU.ods")))

    def test_extranjero_sin_decidir_a_revisar_y_con_clave_entra_como_isp(self):
        def varios(d):
            for linea in d["lineas"]:
                linea["descripcion_original"] = linea["short_name"] = "Varios"
        ruta = caso("compras__intracomunitaria.json", varios)
        salida, _ = generar([ruta])
        self.assertIn("CONTASOL_CLAVE_NO_DEDUCIBLE", motivos(salida)[os.path.basename(ruta)])
        salida, _ = generar([ruta], "--clave-extranjero", "SERVICIO_INTRACOMUNITARIO_ISP")
        f = hoja(salida, "IVS")[0]
        self.assertEqual((f["H"], f["BB"]), ("2", "8"))

    def test_asiento_que_no_cuadra_al_centimo_sale_a_revisar(self):
        ruta = caso("compras__identidad.json", lambda d: d["factura"].update(total_factura=181.51))
        salida, _ = generar([ruta])
        self.assertIn("CONTASOL_ASIENTO_NO_CUADRA", motivos(salida)[os.path.basename(ruta)])

    def test_codigo_agotado(self):
        salida, _ = generar([os.path.join(ENTRADA, "compras__identidad.json"),
                             os.path.join(ENTRADA, "compras__suplidos.json")], "--primer-codigo", "99999")
        self.assertEqual(hoja(salida, "IVS")[0]["A"], "99999")
        self.assertIn("CONTASOL_CODIGO_AGOTADO", motivos(salida)["compras__suplidos.json"])

    def test_maestro_con_otra_longitud_es_error_de_configuracion(self):
        d = temporal()
        ruta = os.path.join(d, "maestro.csv")
        with open(ruta, "w", encoding="utf-8") as fh:
            fh.write("nif;subcuenta\nB99999997;4000000001\n")
        _, rc = generar([ENTRADA], "--maestro", ruta, parametros=False)
        self.assertEqual(rc, 2)
        _, rc = generar([ENTRADA], "--maestro", ruta, "--digitos", "10", parametros=False)
        self.assertEqual(rc, 0)

    def test_texto_raro_va_como_texto_y_sin_romper_el_xml(self):
        ruta = caso("compras__identidad.json",
                    lambda d: d["proveedor"].update(nombre="=HYPERLINK(\"x\")\x07 <b>&amp; \"SL\""))
        salida, _ = generar([ruta])
        f = hoja(salida, "IVS")[0]
        self.assertEqual(f["F"], "=HYPERLINK(\"x\") <b>&amp; \"SL\"")


def ej(*nombres):
    return [os.path.join(ENTRADA, n) for n in nombres]


def directorio_con(*rutas):
    """Un directorio temporal con solo esas entradas (la vía B lee un directorio)."""
    d = temporal()
    for r in rutas:
        shutil.copy(r, d)
    return d


class DuplicadasYRedondeo(unittest.TestCase):
    """La misma factura dos veces en el lote, y 1-2 céntimos de redondeo, con sus contrarios."""

    def test_la_segunda_duplicada_a_revisar_y_la_primera_entra(self):
        salida, rc = generar(ej("compras__duplicada_a.json", "compras__duplicada_b.json"))
        self.assertEqual(rc, 0)
        m = motivos(salida)
        self.assertIn("POSIBLE_DUPLICADA", m.get("compras__duplicada_b.json", ""))
        self.assertNotIn("compras__duplicada_a.json", m)
        self.assertEqual([f["E"] for f in hoja(salida, "IVS")], ["P-2026-3001"])

    def test_mismo_proveedor_y_fecha_con_otro_numero_no_es_duplicada(self):
        otra = caso("compras__duplicada_b.json", lambda d: d["factura"].update(numero="P-2026-3009"))
        salida, _ = generar(ej("compras__duplicada_a.json") + [otra])
        self.assertEqual(motivos(salida), {})
        self.assertEqual(sorted(f["E"] for f in hoja(salida, "IVS")), ["P-2026-3001", "P-2026-3009"])

    def test_un_centimo_de_base_o_de_cuota_entra_cuadrando_con_la_cabecera_y_con_aviso(self):
        salida, _ = generar(ej("compras__redondeo_base.json", "compras__redondeo_cuota.json"))
        self.assertEqual(motivos(salida), {})
        ivs = {f["E"]: f for f in hoja(salida, "IVS")}
        self.assertEqual((ivs["P-2026-3002"]["J"], ivs["P-2026-3002"]["S"]), ("76,54", "16,08"))
        self.assertEqual((ivs["P-2026-3003"]["J"], ivs["P-2026-3003"]["S"]), ("76,53", "16,07"))
        self.assertEqual(len(hoja(salida, "APU")), 6)
        avisos = [(a["documento"], a["codigo"]) for a in filas_csv(os.path.join(salida, "avisos.csv"))]
        self.assertIn(("compras__redondeo_base.json", "AJUSTE_REDONDEO"), avisos)
        self.assertIn(("compras__redondeo_cuota.json", "AJUSTE_REDONDEO"), avisos)

    def test_sin_redondeo_no_hay_aviso_de_ajuste(self):
        salida, _ = generar(ej("compras__identidad.json"))
        self.assertNotIn("AJUSTE_REDONDEO", [a["codigo"] for a in filas_csv(os.path.join(salida, "avisos.csv"))])

    def test_tres_centimos_sale_a_revisar_y_sin_aviso_de_ajuste(self):
        salida, _ = generar(ej("compras__descuadre_3c.json"))
        self.assertIn("DESGLOSE_NO_CUADRA", motivos(salida).get("compras__descuadre_3c.json", ""))
        self.assertEqual(hoja(salida, "IVS"), None)
        self.assertNotIn("AJUSTE_REDONDEO", [a["codigo"] for a in filas_csv(os.path.join(salida, "avisos.csv"))])

    def test_via_b_rojo_si_la_duplicada_no_esta_apartada(self):
        entradas = ej("compras__duplicada_a.json", "compras__duplicada_b.json")
        salida, _ = generar(entradas)
        self.assertEqual(correr_via_b(salida, directorio_con(*entradas)).returncode, 0)
        ruta = os.path.join(salida, "revisar.csv")
        with open(ruta, encoding="utf-8-sig") as fh:
            lineas = [x for x in fh if "compras__duplicada_b.json" not in x]
        with open(ruta, "w", encoding="utf-8") as fh:
            fh.writelines(lineas)
        r = correr_via_b(salida, directorio_con(*entradas))
        self.assertEqual(r.returncode, 1)
        self.assertIn("POSIBLE_DUPLICADA", r.stdout)

    def test_via_b_rojo_si_falta_el_aviso_de_redondeo(self):
        entradas = ej("compras__redondeo_base.json")
        salida, _ = generar(entradas)
        ruta = os.path.join(salida, "avisos.csv")
        with open(ruta, encoding="utf-8-sig") as fh:
            lineas = [x for x in fh if "AJUSTE_REDONDEO" not in x]
        with open(ruta, "w", encoding="utf-8") as fh:
            fh.writelines(lineas)
        r = correr_via_b(salida, directorio_con(*entradas))
        self.assertEqual(r.returncode, 1)
        self.assertIn("AJUSTE_REDONDEO", r.stdout)

    def test_via_b_rojo_si_el_ivs_no_cuadra_exacto_con_la_cabecera(self):
        entradas = ej("compras__redondeo_base.json")
        salida, _ = generar(entradas)
        movida = caso("compras__redondeo_base.json", lambda d: d["factura"].update(base_total=76.55))
        destino = directorio_con(movida)
        os.rename(os.path.join(destino, os.path.basename(movida)), os.path.join(destino, "compras__redondeo_base.json"))
        r = correr_via_b(salida, destino)
        self.assertEqual(r.returncode, 1)
        self.assertIn("cuadrar exacto", r.stdout)


if __name__ == "__main__":
    unittest.main()
