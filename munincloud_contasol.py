#!/usr/bin/env python3
"""munincloud_contasol.py — de la respuesta v1 de la API de auditoría de facturas de MuninCloud a Contasol.

Escribe los dos ficheros que importa Contasol para las facturas RECIBIDAS (compras):

  - `IVS.ods`  libro de IVA soportado, una fila por factura (columnas A..BV de la especificación de DELSOL, ed. 2018).
  - `APU.ods`  los apuntes del diario de cada factura, enlazados con su registro de IVA: `APU.N` = `IVS.A`.
               Solo si das el diario, la cuenta de IVA soportado (472), la de gasto (6xx) y, si el lote lleva
               retenciones, la de retenciones (4751). Si falta alguna, no hay APU y se dice (CONTASOL_SIN_ASIENTO).

Y aparte, en CSV con «;»:

  - `revisar.csv`   lo que NO entra en los .ods, con sus motivos (los de la pieza común y los de Contasol).
  - `omitidos.csv`  lo que no es una factura (trabajos sin terminar, rechazados, clasificaciones).
  - `avisos.csv`    lo que entra pero hay que saber: un número o un nombre recortado a su máximo, el libro supuesto…

Reglas de Contasol que este conector respeta (especificación pública de DELSOL, ed. 2018):
  - un texto más largo que su máximo hace que Contasol DESCARTE la fila entera sin avisar: aquí cada texto se acota a
    su máximo y se avisa; el número de factura se recorta por la cola y va completo en `BV` (observaciones);
  - el libro de IVA guarda TRES tipos de IVA por factura: con más, la factura sale para revisar;
  - las EMITIDAS no se escriben: el libro de IVA repercutido (IVR) no está publicado y meterlas en el IVS sería
    contabilizar una venta como compra.

Solo biblioteca estándar de Python (3.8 o posterior). La pieza común `munincloud_v1.py` hace la lectura y el cuadre.
"""
import argparse
import csv
import os
import re
import sys
import zipfile
from decimal import Decimal
from xml.sax.saxutils import escape

import munincloud_v1 as v1

VERSION = "1.0.0"

# ─── la especificación (transcrita del PDF de DELSOL, ed. 2018) ──────────────
# Máximo de cada TEXTO que se escribe. Pasarse descarta la fila entera en Contasol, sin aviso.
MAX_TEXTO_IVS = {"D": 10, "E": 12, "F": 100, "G": 12, "BS": 2, "BV": 32000}
MAX_TEXTO_APU = {"E": 10, "G": 60, "K": 1, "M": 1}
# Ancho de cada NÚMERO (tal como se ve: coma decimal y signo). Un número no se recorta: si no cabe, a revisar.
ANCHO_NUM_IVS = dict({c: 15 for c in ("J", "K", "L", "S", "T", "U", "V", "W", "X", "Y", "AU", "BT")},
                     **{c: 5 for c in ("M", "N", "O", "P", "Q", "R", "AT")}, A=5, B=1, H=1, I=1, BB=2, BC=1, BD=1)
ANCHO_NUM_APU = {"A": 3, "C": 5, "D": 6, "F": 15, "I": 15, "J": 15, "L": 1, "N": 5}
COLUMNAS_IVS, COLUMNAS_APU = 74, 14          # hasta BV y hasta N: lo que se escribe
MAX_CODIGO = 99999                           # IVS.A y APU.N: 5 dígitos, campo índice sin duplicados
DIGITOS_MAX = 10                             # «Nº de dígitos máximo para cuentas auxiliares», máximo 10

# H · tipo de operación
INTERIOR, IMPORTACION, INTRACOMUNITARIO = 0, 1, 2
# BB · clave de operación (las que se reconocen; la lista entera en la especificación)
# La clave 4 (rectificativa) no se usa: las rectificativas van siempre a revisar (CONTASOL_RECTIFICATIVA).
CLAVE_HABITUAL, CLAVE_VARIOS_TIPOS, CLAVE_ISP, CLAVE_INTRACOM_BIENES = 0, 3, 8, 17
# BC · identificación fiscal
ID_SIN_DEFINIR, ID_NIF, ID_NIF_IVA_INTRACOM = 0, 1, 2

CLAVES_EXTRANJERO = (v1.ADQUISICION_INTRACOMUNITARIA_BIENES, v1.SERVICIO_INTRACOMUNITARIO_ISP,
                     v1.IMPORTACION_BIENES, v1.SERVICIO_EXTRACOMUNITARIO_ISP)
_DIGITOS = re.compile(r"^\d{1,10}$")
_NO_XML = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f￾￿]")


# ─── columnas ─────────────────────────────────────────────────────────────────

def indice(letra):
    """'A' -> 0, 'Z' -> 25, 'AA' -> 26, 'BV' -> 73."""
    n = 0
    for ch in letra:
        n = n * 26 + (ord(ch) - 64)
    return n - 1


def numero(valor):
    """Una celda numérica a partir de un Decimal (o int): ('n', '12.30'). Importes con dos decimales."""
    return ("n", v1.importe(Decimal(valor)))


def porcentaje(valor):
    """Una celda de porcentaje: 21 -> '21', 5.2 -> '5.2', 1.75 -> '1.75' (como mucho dos decimales)."""
    t = format(Decimal(valor).quantize(v1.CENTIMO), "f")
    t = t.rstrip("0").rstrip(".") if "." in t else t
    return ("n", "0" if t in ("", "-0") else t)


def entero(valor):
    return ("n", str(int(valor)))


def visible(celda):
    """Lo que se ve en la celda (y lo que mide su ancho): coma decimal."""
    tipo, valor = celda
    if tipo == "n":
        return valor.replace(".", ",")
    if tipo == "d":
        return f"{valor[8:10]}/{valor[5:7]}/{valor[0:4]}"
    return valor


def limpio(valor):
    """Un texto de una línea, sin caracteres que el XML no admite."""
    return " ".join(_NO_XML.sub("", "" if valor is None else str(valor)).split())


# ─── el lote ──────────────────────────────────────────────────────────────────

def cuenta_ajustada(valor, digitos, nombre):
    """'472' -> '47200000' con 8 dígitos. Solo dígitos; '' si no se da; error si no cabe."""
    if not valor:
        return ""
    if not _DIGITOS.match(str(valor)):
        raise ValueError(f"{nombre} «{valor}» no es una cuenta: solo dígitos, como mucho {DIGITOS_MAX}")
    c = v1.ajustar_longitud(valor, digitos)
    if len(c) != digitos:
        raise ValueError(f"{nombre} «{valor}» tiene {len(c)} dígitos y la empresa usa {digitos} (--digitos)")
    return c


def leer_maestro(ruta, digitos):
    """El fichero «NIF;subcuenta» de tus proveedores -> {nif: subcuenta}. Cada subcuenta, a la longitud exacta."""
    maestro = v1.leer_maestro(ruta, formato=_DIGITOS, maximo=DIGITOS_MAX, que="subcuenta",
                              no_valido="no es una subcuenta: solo dígitos",
                              demasiado_largo=f"la subcuenta tiene más de {DIGITOS_MAX} dígitos")
    for nif, cuenta in maestro.items():
        if len(cuenta) != digitos:
            raise ValueError(f"{ruta}: la subcuenta {cuenta} de {nif} tiene {len(cuenta)} dígitos y la empresa usa "
                             f"{digitos} (--digitos): Contasol la exige al tamaño de los niveles de cuentas")
    return maestro


def nif_contasol(contraparte):
    """(G, BC, BS). Un NIF-IVA de la UE va SIN su prefijo en G y con el país en BS (G admite 12: FR+11 no cabe)."""
    nif, pais = contraparte["nif"], contraparte["pais"]
    if pais and pais != "ES" and nif.startswith(pais):
        return nif[len(pais):], ID_NIF_IVA_INTRACOM, pais
    return nif, (ID_NIF if nif else ID_SIN_DEFINIR), ""


def motivos_contasol(a, cuenta, clave, extranjero_sin_iva, opciones):
    """Lo que Contasol no puede recibir tal cual, además de lo que ya aparta la pieza común."""
    m = []
    if a["es_rectificativa"]:
        m.append(("CONTASOL_RECTIFICATIVA", "rectificativa: siempre a revisar; el bloque de la factura rectificada del "
                                            "IVS (AA-AR) no tiene posiciones publicadas y sin él el modelo 303 puede "
                                            f"salir mal (rectifica «{a['rectifica_numero'] or 'número no leído'}»)"))
    if a["direccion"] == v1.EMITIDA:
        m.append(("CONTASOL_VENTA_SIN_LIBRO", "es una factura emitida: el libro de IVA repercutido (IVR) de Contasol no "
                                              "está publicado y en el IVS sería una venta contabilizada como compra"))
        return m
    if a["direccion"] != v1.RECIBIDA:
        return m
    tipos = [r for r in a["registros"] if r["clase"] == "IVA"]
    if len(tipos) > 3:
        m.append(("CONTASOL_DEMASIADOS_TIPOS", f"tiene {len(tipos)} tipos de IVA y el libro de Contasol guarda 3: "
                                               "a medias el asiento no cuadraría"))
    if not cuenta:
        m.append(("CONTASOL_SIN_CUENTA_PROVEEDOR", f"el NIF {a['contraparte']['nif'] or '(sin NIF)'} no está en el "
                                                   "maestro y no se dio --cuenta-proveedor"))
    if extranjero_sin_iva and not clave:
        m.append(("CONTASOL_CLAVE_NO_DEDUCIBLE", "proveedor no español sin IVA español y v1 no dice si es un bien o un "
                                                 "servicio: pon --clave-extranjero o contabilízala a mano"))
    g = nif_contasol(a["contraparte"])[0]
    if len(g) > MAX_TEXTO_IVS["G"]:
        m.append(("CONTASOL_NIF_DEMASIADO_LARGO", f"el NIF {g} tiene {len(g)} caracteres y Contasol admite "
                                                  f"{MAX_TEXTO_IVS['G']}: un NIF recortado es otro NIF"))
    return m


def operacion(a, clave, n_tipos):
    """(H, BB) con precedencia explícita: intracomunitaria e ISP antes que varios tipos (las rectificativas no llegan
    aquí: van a revisar). Es una DECISIÓN (una factura puede encajar en dos claves y Contasol admite una), la misma que el portal."""
    pais = a["contraparte"]["pais"]
    if clave == v1.IMPORTACION_BIENES:
        h = IMPORTACION
    elif clave in (v1.ADQUISICION_INTRACOMUNITARIA_BIENES, v1.SERVICIO_INTRACOMUNITARIO_ISP):
        h = INTRACOMUNITARIO
    else:
        h = INTRACOMUNITARIO if (pais and pais != "ES") else INTERIOR
    if clave == v1.ADQUISICION_INTRACOMUNITARIA_BIENES:
        bb = CLAVE_INTRACOM_BIENES
    elif clave in (v1.SERVICIO_INTRACOMUNITARIO_ISP, v1.SERVICIO_EXTRACOMUNITARIO_ISP):
        bb = CLAVE_ISP
    elif n_tipos > 1:
        bb = CLAVE_VARIOS_TIPOS
    else:
        bb = CLAVE_HABITUAL
    return h, bb


def texto_acotado(valor, maximo, por_la_cola=False):
    """(texto, recortado). El número de factura se recorta por la cola (lo que distingue suele ir al final)."""
    t = limpio(valor)
    if len(t) <= maximo:
        return t, False
    return (t[-maximo:] if por_la_cola else t[:maximo]), True


def fila_ivs(codigo, a, cuenta, clave, opciones):
    """{letra: celda} del registro de IVA soportado y la lista de avisos (codigo, mensaje)."""
    avisos, obs = [], []
    tipos = [r for r in a["registros"] if r["clase"] == "IVA"]
    h, bb = operacion(a, clave, len(tipos))
    g, bc, bs = nif_contasol(a["contraparte"])
    f = {"A": entero(codigo), "B": entero(opciones["libro_iva"]), "D": ("s", cuenta), "G": ("s", g),
         "H": entero(h), "I": entero(0), "Y": numero(a["total"]), "BB": entero(bb), "BC": entero(bc), "BD": entero(0)}
    if a["fecha"]:
        f["C"] = f["AX"] = ("d", a["fecha"])
    num, recortado = texto_acotado(a["numero"], MAX_TEXTO_IVS["E"], por_la_cola=True)
    f["E"] = ("s", num)
    if recortado:
        obs.append(f"Número completo de factura: {limpio(a['numero'])}")
        avisos.append(("CONTASOL_NUMERO_RECORTADO", f"el número «{limpio(a['numero'])}» pasa de "
                                                    f"{MAX_TEXTO_IVS['E']} caracteres: va como «{num}» y completo en BV"))
    nombre, recortado = texto_acotado(a["contraparte"]["nombre"], MAX_TEXTO_IVS["F"])
    f["F"] = ("s", nombre)
    if recortado:
        avisos.append(("CONTASOL_CAMPO_RECORTADO", f"el nombre del proveedor pasa de {MAX_TEXTO_IVS['F']} caracteres "
                                                   "y va recortado (F)"))
    for (cb, ct, cc, cpr, ccr), r in zip((("J", "M", "S", "P", "V"), ("K", "N", "T", "Q", "W"),
                                          ("L", "O", "U", "R", "X")), tipos):
        f[cb], f[ct], f[cc] = numero(r["base"]), porcentaje(r["tipo_iva"]), numero(r["cuota"])
        if r["tipo_recargo"] is not None and r["cuota_recargo"] is not None:
            f[cpr], f[ccr] = porcentaje(r["tipo_recargo"]), numero(r["cuota_recargo"])
    if a["retencion"]:
        if a["porcentaje_retencion"] is not None:
            f["AT"] = porcentaje(a["porcentaje_retencion"])
        f["AU"] = numero(a["retencion"])
    if bs:
        f["BS"] = ("s", bs)
    if a["suplidos"]:
        f["BT"] = numero(a["suplidos"])
    if obs:
        f["BV"] = ("s", texto_acotado(". ".join(obs), MAX_TEXTO_IVS["BV"])[0])
    return f, avisos


def apuntes(codigo, a, cuenta_proveedor, opciones):
    """Las filas del APU de una factura, o (None, descuadre) si debe ≠ haber."""
    tipos = [r for r in a["registros"] if r["clase"] == "IVA"]
    gasto = sum((r["base"] for r in tipos), Decimal("0")) + a["suplidos"]
    iva = sum((r["cuota"] + (r["cuota_recargo"] or 0) for r in tipos), Decimal("0"))
    ret = a["retencion"] or Decimal("0")
    lineas = [(opciones["cuenta_gasto"], gasto, Decimal("0"))]
    if iva:
        lineas.append((opciones["cuenta_iva"], iva, Decimal("0")))
    if ret:
        lineas.append((opciones["cuenta_retencion"], Decimal("0"), ret))
    lineas.append((cuenta_proveedor, Decimal("0"), a["total"]))
    debe = sum((d for _, d, _ in lineas), Decimal("0"))
    haber = sum((h for _, _, h in lineas), Decimal("0"))
    if v1.redondear(debe) != v1.redondear(haber):
        return None, v1.redondear(debe - haber)
    concepto = texto_acotado(f"Fra. {texto_acotado(a['numero'], MAX_TEXTO_IVS['E'], True)[0]} "
                             f"{limpio(a['contraparte']['nombre'])}", MAX_TEXTO_APU["G"])[0]
    filas = []
    for orden, (cuenta, d, h) in enumerate(lineas, 1):
        fila = {"A": entero(opciones["diario"]), "C": entero(0), "D": entero(orden), "E": ("s", cuenta),
                "F": numero(0), "G": ("s", concepto), "I": numero(d), "J": numero(h), "K": ("s", "E"),
                "L": entero(0), "M": ("s", "S"), "N": entero(codigo)}
        if a["fecha"]:
            fila["B"] = ("d", a["fecha"])
        filas.append(fila)
    return filas, Decimal("0")


def demasiado_ancho(fila, anchos, textos):
    """Las columnas cuyo valor no cabe (números por ancho visible, textos por su máximo)."""
    malas = []
    for letra, celda in fila.items():
        tope = anchos.get(letra) if celda[0] == "n" else textos.get(letra)
        if tope is not None and len(visible(celda)) > tope:
            malas.append(letra)
    return malas


def convertir(entradas, mi_nif="", direccion="", maestro=None, opciones=None):
    """Todo el lote: {ivs, apu (None si no hay asiento), revisar, omitidos, avisos}."""
    o = dict({"libro_iva": None, "diario": 0, "cuenta_iva": "", "cuenta_gasto": "", "cuenta_retencion": "",
              "cuenta_proveedor": "", "clave_extranjero": "", "primer_codigo": 1}, **(opciones or {}))
    maestro = maestro or {}
    avisos, revisar, omitidos, ivs, apu = [], [], [], [], []
    if not o["libro_iva"]:
        o["libro_iva"] = 1
        avisos.append(["", "", "CONTASOL_LIBRO_IVA_SUPUESTO", "no se dio --libro-iva: el IVS lleva el libro 1"])
    analizadas = []
    dup = v1.duplicadas(entradas, mi_nif, direccion)       # la misma factura dos veces en el lote: la 2.ª a revisar
    for e, duplicada_de in zip(entradas, dup):
        if e["documento"] is None:
            omitidos.append([e["origen"], e["trabajo"], e["omitido"]])
            continue
        analizadas.append((e, v1.analizar(e["documento"], mi_nif, direccion, duplicada_de)))
    falta = ("la cuenta de IVA soportado (--cuenta-iva)" if not o["cuenta_iva"] else
             "el diario (--diario)" if not o["diario"] else
             "la cuenta de gasto (--cuenta-gasto)" if not o["cuenta_gasto"] else
             "la cuenta de retenciones (--cuenta-retencion), y el lote lleva retenciones"
             if (not o["cuenta_retencion"] and any(a["retencion"] for _, a in analizadas
                                                   if a["direccion"] == v1.RECIBIDA)) else "")
    if falta:
        avisos.append(["", "", "CONTASOL_SIN_ASIENTO", f"no se genera APU.ods: falta {falta}; el IVS se importa igual"])
    codigo = o["primer_codigo"] - 1
    for e, a in analizadas:
        doc = e["documento"]
        cuenta = maestro.get(a["contraparte"]["nif"], "") or o["cuenta_proveedor"]
        extranjero_sin_iva = (a["direccion"] == v1.RECIBIDA and a["contraparte"]["pais"] != "ES"
                              and abs(a["iva"] or 0) < Decimal("0.005"))
        clave = None
        if extranjero_sin_iva:
            clave = v1.clave_operacion(doc, mi_nif, direccion)["clave"] or o["clave_extranjero"] or None
        motivos = a["motivos"] + motivos_contasol(a, cuenta, clave, extranjero_sin_iva, o)
        filas_apu = None
        if not motivos:
            if codigo + 1 > MAX_CODIGO:
                motivos.append(("CONTASOL_CODIGO_AGOTADO", f"IVS.A admite hasta {MAX_CODIGO}: usa --primer-codigo o "
                                                           "parte el lote"))
            else:
                fila, av = fila_ivs(codigo + 1, a, cuenta, clave, o)
                anchas = demasiado_ancho(fila, ANCHO_NUM_IVS, MAX_TEXTO_IVS)
                if not falta:
                    filas_apu, descuadre = apuntes(codigo + 1, a, cuenta, o)
                    if filas_apu is None:
                        motivos.append(("CONTASOL_ASIENTO_NO_CUADRA", f"debe y haber difieren en {descuadre}"))
                    else:
                        for fa in filas_apu:
                            anchas += [f"APU.{x}" for x in demasiado_ancho(fa, ANCHO_NUM_APU, MAX_TEXTO_APU)]
                if anchas:
                    motivos.append(("CONTASOL_CAMPO_DEMASIADO_LARGO", "no cabe en su columna: " + ", ".join(anchas)))
        if motivos:
            revisar.append([e["origen"], e["trabajo"], a["direccion"], a["numero"],
                            " | ".join(c for c, _ in motivos), " | ".join(t for _, t in motivos)])
            continue
        codigo += 1
        ivs.append(fila)
        apu += filas_apu or []
        avisos += [[e["origen"], e["trabajo"], c, t] for c, t in av]
        # AJUSTE_REDONDEO (1-2 céntimos absorbidos por la pieza común): aviso, no motivo; la factura entra igual.
        avisos += [[e["origen"], e["trabajo"], c, t] for c, t in a.get("notas", [])]
    return {"ivs": ivs, "apu": None if falta else apu, "revisar": revisar, "omitidos": omitidos, "avisos": avisos}


# ─── el .ods ──────────────────────────────────────────────────────────────────

MIMETYPE = "application/vnd.oasis.opendocument.spreadsheet"
_MANIFIESTO = (
    '<?xml version="1.0" encoding="UTF-8"?>\n'
    '<manifest:manifest xmlns:manifest="urn:oasis:names:tc:opendocument:xmlns:manifest:1.0" manifest:version="1.2">\n'
    f' <manifest:file-entry manifest:full-path="/" manifest:version="1.2" manifest:media-type="{MIMETYPE}"/>\n'
    ' <manifest:file-entry manifest:full-path="content.xml" manifest:media-type="text/xml"/>\n'
    '</manifest:manifest>\n')
_CABECERA = (
    '<?xml version="1.0" encoding="UTF-8"?>\n'
    '<office:document-content xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0"'
    ' xmlns:style="urn:oasis:names:tc:opendocument:xmlns:style:1.0"'
    ' xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0"'
    ' xmlns:table="urn:oasis:names:tc:opendocument:xmlns:table:1.0"'
    ' xmlns:number="urn:oasis:names:tc:opendocument:xmlns:datastyle:1.0" office:version="1.2">\n'
    '<office:automatic-styles>'
    '<number:date-style style:name="N1"><number:day number:style="long"/><number:text>/</number:text>'
    '<number:month number:style="long"/><number:text>/</number:text><number:year number:style="long"/>'
    '</number:date-style>'
    '<style:style style:name="FECHA" style:family="table-cell" style:data-style-name="N1"/>'
    '</office:automatic-styles>\n<office:body><office:spreadsheet>\n')


def _celda(celda):
    if celda is None:
        return "<table:table-cell/>"
    tipo, valor = celda
    p = f"<text:p>{escape(visible(celda))}</text:p>"
    if tipo == "n":
        return f'<table:table-cell office:value-type="float" office:value="{valor}">{p}</table:table-cell>'
    if tipo == "d":
        return (f'<table:table-cell table:style-name="FECHA" office:value-type="date" office:date-value="{valor}">'
                f'{p}</table:table-cell>')
    return f'<table:table-cell office:value-type="string">{p}</table:table-cell>'


def contenido_ods(nombre, filas, columnas):
    """El content.xml de una hoja: sin cabecera (Contasol lee datos desde la fila que le digas), una fila por registro."""
    partes = [_CABECERA, f'<table:table table:name="{nombre}">',
              f'<table:table-column table:number-columns-repeated="{columnas}"/>\n']
    for fila in filas:
        ultima = max(indice(letra) for letra in fila)
        celdas = [None] * (ultima + 1)
        for letra, celda in fila.items():
            celdas[indice(letra)] = celda
        partes.append("<table:table-row>" + "".join(_celda(c) for c in celdas) + "</table:table-row>\n")
    partes.append("</table:table>\n</office:spreadsheet></office:body></office:document-content>\n")
    return "".join(partes).encode("utf-8")


def escribir_ods(ruta, nombre, filas, columnas):
    """ZIP con `mimetype` PRIMERO y sin comprimir. Todo sin comprimir y con fecha fija: el mismo lote da el mismo
    fichero byte a byte, en cualquier máquina (sin depender de la versión de zlib)."""
    with zipfile.ZipFile(ruta, "w") as z:
        for entrada, datos in (("mimetype", MIMETYPE.encode("ascii")), ("META-INF/manifest.xml", _MANIFIESTO.encode()),
                               ("content.xml", contenido_ods(nombre, filas, columnas))):
            info = zipfile.ZipInfo(entrada, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type, info.create_system, info.external_attr = zipfile.ZIP_STORED, 3, 0o644 << 16
            z.writestr(info, datos)


def _escribir_csv(ruta, cabecera, filas):
    with open(ruta, "w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh, delimiter=";", quoting=csv.QUOTE_ALL, lineterminator="\r\n")
        w.writerow(cabecera)
        for f in filas:
            w.writerow([v1.celda_segura(c) for c in f])


# ─── la orden ─────────────────────────────────────────────────────────────────

def main(argv=None):
    p = argparse.ArgumentParser(prog="munincloud_contasol",
                                description="De respuestas v1 de la API de MuninCloud a IVS.ods + APU.ods de Contasol "
                                            "(solo facturas recibidas).")
    p.add_argument("entradas", nargs="+", help="ficheros .json o directorios con las respuestas de la API")
    p.add_argument("--salida", required=True, help="directorio donde escribir los ficheros")
    p.add_argument("--mi-nif", default="", help="el NIF de tu empresa: comprueba la dirección que da la API")
    p.add_argument("--direccion", choices=["RECIBIDA", "EMITIDA"], default="",
                   help="fuerza la dirección de TODAS las facturas (manda sobre la que da la API)")
    p.add_argument("--maestro", default="", help="fichero «NIF;subcuenta» de tus proveedores (IVS.D)")
    p.add_argument("--digitos", type=int, default=8,
                   help="dígitos de las cuentas auxiliares de tu empresa en Contasol (1-10; por defecto 8)")
    p.add_argument("--cuenta-proveedor", default="", help="subcuenta para el proveedor que no esté en el maestro")
    p.add_argument("--libro-iva", type=int, default=0, help="número de libro de IVA soportado (IVS.B, 1-9; si no, 1 "
                                                            "con aviso)")
    p.add_argument("--diario", type=int, default=0, help="diario de las compras (APU.A, 1-999)")
    p.add_argument("--cuenta-iva", default="", help="cuenta de IVA soportado (p. ej. 472)")
    p.add_argument("--cuenta-gasto", default="", help="cuenta de gasto contra la que se apunta (p. ej. 629)")
    p.add_argument("--cuenta-retencion", default="", help="cuenta de retenciones practicadas (p. ej. 4751)")
    p.add_argument("--clave-extranjero", default="", choices=[""] + list(CLAVES_EXTRANJERO),
                   help="clave para el proveedor no español cuando v1 no deja saber si es bien o servicio")
    p.add_argument("--primer-codigo", type=int, default=1, help="primer código de IVS.A (y APU.N); por defecto 1")
    p.add_argument("--version", action="version", version=f"%(prog)s {VERSION} (lectura v1 {v1.VERSION_V1})")
    args = p.parse_args(argv)

    try:
        if not 1 <= args.digitos <= DIGITOS_MAX:
            raise ValueError(f"--digitos tiene que estar entre 1 y {DIGITOS_MAX}")
        if args.libro_iva and not 1 <= args.libro_iva <= 9:
            raise ValueError("--libro-iva tiene que estar entre 1 y 9 (IVS.B es de un dígito)")
        if args.diario and not 1 <= args.diario <= 999:
            raise ValueError("--diario tiene que estar entre 1 y 999 (APU.A es de tres dígitos)")
        if not 1 <= args.primer_codigo <= MAX_CODIGO:
            raise ValueError(f"--primer-codigo tiene que estar entre 1 y {MAX_CODIGO}")
        maestro = leer_maestro(args.maestro, args.digitos) if args.maestro else {}
        opciones = {"libro_iva": args.libro_iva, "diario": args.diario, "clave_extranjero": args.clave_extranjero,
                    "primer_codigo": args.primer_codigo}
        for clave, nombre in (("cuenta_iva", "--cuenta-iva"), ("cuenta_gasto", "--cuenta-gasto"),
                              ("cuenta_retencion", "--cuenta-retencion"), ("cuenta_proveedor", "--cuenta-proveedor")):
            opciones[clave] = cuenta_ajustada(getattr(args, clave), args.digitos, nombre)
    except (OSError, ValueError) as e:
        print(f"error de configuración: {e}", file=sys.stderr)
        return 2

    r = convertir(v1.leer_entradas(args.entradas), args.mi_nif, args.direccion, maestro, opciones)
    os.makedirs(args.salida, exist_ok=True)
    escritos = []
    if r["ivs"]:
        escribir_ods(os.path.join(args.salida, "IVS.ods"), "IVS", r["ivs"], COLUMNAS_IVS)
        escritos.append("IVS.ods")
    if r["apu"]:
        escribir_ods(os.path.join(args.salida, "APU.ods"), "APU", r["apu"], COLUMNAS_APU)
        escritos.append("APU.ods")
    _escribir_csv(os.path.join(args.salida, "revisar.csv"),
                  ["documento", "trabajo", "direccion", "numero", "motivos", "detalle"], r["revisar"])
    _escribir_csv(os.path.join(args.salida, "omitidos.csv"), ["documento", "trabajo", "motivo"], r["omitidos"])
    _escribir_csv(os.path.join(args.salida, "avisos.csv"), ["documento", "trabajo", "codigo", "mensaje"], r["avisos"])
    print(f"ficheros={','.join(escritos) or 'ninguno'} ivs={len(r['ivs'])} apu={len(r['apu'] or [])} "
          f"apartadas={len(r['revisar'])} omitidos={len(r['omitidos'])} avisos={len(r['avisos'])} salida={args.salida}")
    for fila in r["revisar"]:
        print(f"  apartada {fila[0]}: {fila[4]}")
    for fila in r["avisos"]:
        print(f"  aviso    {fila[0] or '(lote)'}: {fila[2]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
