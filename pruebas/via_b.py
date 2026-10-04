#!/usr/bin/env python3
"""Vía B del conector Contasol: OTRO lector de `IVS.ods` y `APU.ods`, que no comparte ni una línea con el conector
ni con la pieza común. Lee el ZIP con `zipfile` y el `content.xml` con `xml.etree`, y las respuestas JSON tal cual.

    python3 pruebas/via_b.py <directorio de salida> <directorio de entradas>

Comprueba, y sale con 1 si algo falla:
  - el .ods: `mimetype` es la 1.ª entrada del ZIP, sin comprimir, con el tipo de hoja de cálculo;
  - cada celda cabe en su columna (textos por su máximo, números por su ancho visible);
  - cada registro del IVS cuadra: bases + cuotas + recargos + suplidos - retención = total;
  - cada `APU.N` existe en `IVS.A`, cada asiento cuadra (debe = haber) y casa con su registro de IVA;
  - cada factura de las entradas está UNA vez: en el IVS (mismo número, fecha y total) o en `revisar.csv`, y lo que no
    es factura, en `omitidos.csv`. Una fila borrada o un importe cambiado no pasan;
  - duplicadas: dos facturas del lote con el mismo NIF del proveedor y del cliente, número (solo A-Z0-9) y
    fecha son la MISMA; la 2.ª y siguientes (por nombre de fichero) están en `revisar.csv` con POSIBLE_DUPLICADA, no en
    el IVS;
  - redondeo: todo registro del IVS cuadra EXACTO con la cabecera (J+K+L = base, S+T+U = IVA); si las líneas
    (todas con tipo > 0) difieren de la cabecera en 1-2 céntimos, la factura NO se aparta por descuadre y, si entra,
    lleva el aviso AJUSTE_REDONDEO en `avisos.csv`; si difieren en más de 2, está en `revisar.csv`;
  - rectificativas: toda factura con `es_rectificativa` está en `revisar.csv` y nunca en el IVS, y ningún registro
    del IVS lleva la clave 4 (rectificativa) en `BB`.
"""
import csv
import json
import os
import re
import sys
import xml.etree.ElementTree as ET
import zipfile
from decimal import ROUND_HALF_UP, Decimal

NS = {"office": "urn:oasis:names:tc:opendocument:xmlns:office:1.0",
      "table": "urn:oasis:names:tc:opendocument:xmlns:table:1.0",
      "text": "urn:oasis:names:tc:opendocument:xmlns:text:1.0"}
TIPO_ODS = "application/vnd.oasis.opendocument.spreadsheet"
# Especificación de DELSOL (ed. 2018), transcrita aparte de la del conector a propósito.
TEXTO_IVS = {"D": 10, "E": 12, "F": 100, "G": 12, "BS": 2, "BV": 32000}
TEXTO_APU = {"E": 10, "G": 60, "H": 5, "K": 1, "M": 1, "Q": 255}
NUM_IVS = {"A": 5, "B": 1, "H": 1, "I": 1, "Y": 15, "AT": 5, "AU": 15, "BB": 2, "BC": 1, "BD": 1, "BT": 15,
           "J": 15, "K": 15, "L": 15, "M": 5, "N": 5, "O": 5, "P": 5, "Q": 5, "R": 5,
           "S": 15, "T": 15, "U": 15, "V": 15, "W": 15, "X": 15}
NUM_APU = {"A": 3, "C": 5, "D": 6, "F": 15, "I": 15, "J": 15, "L": 1, "N": 5}
CERO = Decimal("0")
fallos = []


def falla(texto):
    fallos.append(texto)


def letra(i):
    s = ""
    i += 1
    while i:
        i, r = divmod(i - 1, 26)
        s = chr(65 + r) + s
    return s


def leer_ods(ruta, hoja):
    """[{letra: (tipo, valor, texto)}] de la hoja, o None si el fichero no es un .ods válido."""
    with zipfile.ZipFile(ruta) as z:
        info = z.infolist()
        if not info or info[0].filename != "mimetype":
            falla(f"{hoja}: la 1.ª entrada del ZIP no es mimetype ({info[0].filename if info else 'vacío'})")
        elif info[0].compress_type != zipfile.ZIP_STORED:
            falla(f"{hoja}: mimetype va comprimido")
        elif z.read("mimetype").decode("ascii", "replace") != TIPO_ODS:
            falla(f"{hoja}: mimetype no es {TIPO_ODS}")
        raiz = ET.fromstring(z.read("content.xml"))
    tablas = raiz.findall(".//table:table", NS)
    if len(tablas) != 1 or tablas[0].get(f"{{{NS['table']}}}name") != hoja:
        falla(f"{hoja}: se esperaba una sola hoja llamada {hoja}")
        return []
    filas = []
    for tr in tablas[0].findall("table:table-row", NS):
        fila, col = {}, 0
        for tc in tr.findall("table:table-cell", NS):
            rep = int(tc.get(f"{{{NS['table']}}}number-columns-repeated", "1"))
            tipo = tc.get(f"{{{NS['office']}}}value-type")
            txt = "".join("".join(p.itertext()) for p in tc.findall("text:p", NS))
            valor = (tc.get(f"{{{NS['office']}}}value") if tipo == "float" else
                     tc.get(f"{{{NS['office']}}}date-value") if tipo == "date" else txt)
            for _ in range(rep):
                if tipo:
                    fila[letra(col)] = (tipo, valor, txt)
                col += 1
        rep_f = int(tr.get(f"{{{NS['table']}}}number-rows-repeated", "1"))
        if fila:
            filas += [fila] * rep_f
    return filas


def num(fila, c):
    v = fila.get(c)
    if v is None:
        return CERO
    if v[0] != "float":
        falla(f"columna {c}: se esperaba un número y hay «{v[2]}»")
        return CERO
    return Decimal(v[1])


def anchos(nombre, filas, textos, numeros):
    for i, f in enumerate(filas, 1):
        for c, (tipo, valor, txt) in f.items():
            tope = numeros.get(c) if tipo == "float" else textos.get(c) if tipo == "string" else None
            if tope is not None and len(txt) > tope:
                falla(f"{nombre} fila {i} columna {c}: «{txt[:30]}» tiene {len(txt)} y el máximo es {tope}")
            if tipo == "float" and txt != valor.replace(".", ","):
                falla(f"{nombre} fila {i} columna {c}: se ve «{txt}» y vale {valor}")


def nif_esperado(nif, bs):
    n = re.sub(r"[^A-Z0-9]", "", str(nif or "").upper())
    if len(n) == 11 and n.startswith("ES") and re.match(r"^[A-Z0-9]\d{7}[A-Z0-9]$", n[2:]):
        n = n[2:]
    if bs and n.startswith(bs) and not re.match(r"^[A-Z0-9]\d{7}[A-Z0-9]$", n):
        n = n[2:]
    return n


def csv_documentos(ruta):
    if not os.path.exists(ruta):
        return None
    with open(ruta, encoding="utf-8-sig", newline="") as fh:
        return [f["documento"] for f in csv.DictReader(fh, delimiter=";")]


def csv_filas(ruta):
    if not os.path.exists(ruta):
        return None
    with open(ruta, encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh, delimiter=";"))


def cent(x):
    return Decimal(str(x if x is not None else 0)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def desvio_lineas(doc):
    """(dif. de base, dif. de IVA) entre lo que dan las líneas y la cabecera, o None si no se puede medir aquí
    (alguna línea sin tipo o al 0 %: suplidos, exentas… quedan fuera de esta comprobación a propósito)."""
    fac, lin = doc["factura"], doc.get("lineas") or []
    if not lin or any(not isinstance(x.get("tax_percent"), (int, float)) or x["tax_percent"] <= 0 for x in lin):
        return None
    # la cuota de las líneas es la de cada línea a céntimos, sumada (P-2026-3003: 3 x 5,36 = 16,08 y no 76,53 x 21 %)
    base = sum((Decimal(str(x.get("amount") or 0)) for x in lin), CERO)
    cuota = sum((cent(Decimal(str(x.get("amount") or 0)) * Decimal(str(x["tax_percent"])) / 100) for x in lin), CERO)
    return abs(cent(base) - cent(fac.get("base_total"))), abs(cuota - cent(fac.get("iva_total")))


def clave_dup(doc):
    fac, prov, cli = doc["factura"], doc.get("proveedor") or {}, doc.get("cliente") or {}
    n = lambda v: re.sub(r"[^A-Z0-9]", "", str(v or "").upper())  # noqa: E731
    fecha = str(fac.get("fecha") or "")
    k = (n(prov.get("cif_nif")), n(cli.get("cif_nif")), n(fac.get("numero")), fecha)
    return k if all(k) and re.match(r"^\d{4}-\d{2}-\d{2}$", fecha) else None


def main(argv):
    if len(argv) != 2:
        print("uso: via_b.py <salida> <entradas>", file=sys.stderr)
        return 2
    salida, entradas = argv
    ivs_ruta, apu_ruta = os.path.join(salida, "IVS.ods"), os.path.join(salida, "APU.ods")
    ivs = leer_ods(ivs_ruta, "IVS") if os.path.exists(ivs_ruta) else []
    apu = leer_ods(apu_ruta, "APU") if os.path.exists(apu_ruta) else None
    anchos("IVS", ivs, TEXTO_IVS, NUM_IVS)

    # el IVS por dentro
    codigos = {}
    for i, f in enumerate(ivs, 1):
        a = f.get("A")
        if not a or a[0] != "float" or not re.match(r"^\d+$", a[1]):
            falla(f"IVS fila {i}: sin código entero en A")
            continue
        if a[1] in codigos:
            falla(f"IVS fila {i}: código {a[1]} repetido (A es índice sin duplicados)")
        codigos[a[1]] = f
        suma = sum((num(f, c) for c in "JKLSTUVWX"), CERO) + num(f, "BT") - num(f, "AU")
        if suma != num(f, "Y"):
            falla(f"IVS código {a[1]}: bases+cuotas+recargos+suplidos-retención = {suma} y el total Y = {num(f, 'Y')}")
        if "C" not in f or f["C"][0] != "date" or f.get("AX") != f["C"]:
            falla(f"IVS código {a[1]}: fecha C ausente o distinta de AX")
        if num(f, "BB") == 4:
            falla(f"IVS código {a[1]}: lleva la clave 4 (rectificativa) y las rectificativas van a revisar")

    # el APU contra el IVS
    n_apuntes = 0
    if apu is not None:
        anchos("APU", apu, TEXTO_APU, NUM_APU)
        asientos = {}
        for i, f in enumerate(apu, 1):
            n = f.get("N", ("", "", ""))[1]
            if n not in codigos:
                falla(f"APU fila {i}: N={n or '(vacío)'} no existe en IVS.A")
                continue
            if f.get("M", ("", "", ""))[2] != "S" or f.get("K", ("", "", ""))[2] != "E":
                falla(f"APU fila {i}: M tiene que ser S (soportado) y K, E (euros)")
            asientos.setdefault(n, []).append(f)
            n_apuntes += 1
        for n, fr in sorted(codigos.items(), key=lambda x: int(x[0])):
            ap = asientos.get(n)
            if not ap:
                falla(f"IVS código {n}: sin asiento en el APU")
                continue
            debe, haber = sum((num(x, "I") for x in ap), CERO), sum((num(x, "J") for x in ap), CERO)
            if debe != haber:
                falla(f"asiento {n}: debe {debe} ≠ haber {haber}")
            if any(x.get("B") != fr.get("C") for x in ap):
                falla(f"asiento {n}: la fecha del apunte no es la del registro")
            gasto = sum((num(fr, c) for c in "JKL"), CERO) + num(fr, "BT")
            iva = sum((num(fr, c) for c in "STUVWX"), CERO)
            if num(ap[0], "I") != gasto:
                falla(f"asiento {n}: el gasto al debe {num(ap[0], 'I')} no es base+suplidos {gasto}")
            if iva and not any(num(x, "I") == iva for x in ap[1:]):
                falla(f"asiento {n}: ningún apunte lleva el IVA+recargo del registro ({iva})")
            if num(fr, "AU") and not any(num(x, "J") == num(fr, "AU") for x in ap):
                falla(f"asiento {n}: ningún apunte lleva la retención del registro")
            if num(ap[-1], "J") != num(fr, "Y") or ap[-1].get("E", ("",) * 3)[2] != fr.get("D", ("",) * 3)[2]:
                falla(f"asiento {n}: el último apunte no es el proveedor (D) al haber por el total (Y)")

    # cada factura de las entradas, una vez y en su sitio
    revisar = csv_documentos(os.path.join(salida, "revisar.csv"))
    omitidos = csv_documentos(os.path.join(salida, "omitidos.csv"))
    if revisar is None or omitidos is None:
        falla("falta revisar.csv u omitidos.csv")
        revisar, omitidos = revisar or [], omitidos or []
    rutas = [entradas] if os.path.isfile(entradas) else [os.path.join(entradas, x) for x in sorted(os.listdir(entradas))
                                                        if x.lower().endswith(".json")]
    motivos_rev = {f["documento"]: f.get("motivos", "") for f in csv_filas(os.path.join(salida, "revisar.csv")) or []}
    avisos = csv_filas(os.path.join(salida, "avisos.csv")) or []
    con_ajuste = {f["documento"] for f in avisos if f.get("codigo") == "AJUSTE_REDONDEO"}
    pendientes, no_factura, vistas = [], [], {}
    for ruta in rutas:
        nombre = os.path.basename(ruta)
        with open(ruta, encoding="utf-8") as fh:
            obj = json.load(fh)
        doc = obj.get("documento") if obj.get("estado") == "terminado" else (obj if "factura" in obj else None)
        if isinstance(doc, dict) and isinstance(doc.get("factura"), dict) and isinstance(doc.get("lineas"), list):
            k = clave_dup(doc)
            if k and k in vistas:
                if "POSIBLE_DUPLICADA" not in motivos_rev.get(nombre, ""):
                    falla(f"{nombre}: es la misma factura que {vistas[k]} y no está en revisar.csv con POSIBLE_DUPLICADA")
            elif k:
                vistas[k] = nombre
            if doc["factura"].get("es_rectificativa") and nombre not in revisar:
                falla(f"{nombre}: es una rectificativa y no está en revisar.csv (tiene que ir siempre a revisar)")
            d = desvio_lineas(doc)
            if d and max(d) > Decimal("0.02") and nombre not in revisar:
                falla(f"{nombre}: las líneas difieren de la cabecera en {max(d)} (> 2 céntimos) y no está en revisar.csv")
            elif d and Decimal("0") < max(d) <= Decimal("0.02"):
                m = motivos_rev.get(nombre, "")
                if any(x in m for x in ("NO_CUADRA", "DESCUADRE")) and "AVISO_DESCUADRE" not in m:
                    falla(f"{nombre}: un redondeo de {max(d)} (1-2 céntimos) no aparta y está en revisar.csv por «{m}»")
                if nombre not in revisar and nombre not in con_ajuste:
                    falla(f"{nombre}: entra con un redondeo de {max(d)} y avisos.csv no lleva su AJUSTE_REDONDEO")
            if nombre not in revisar:
                pendientes.append((nombre, doc))
        else:
            no_factura.append(nombre)
    for nombre in no_factura:
        if nombre not in omitidos:
            falla(f"{nombre}: no es una factura y no está en omitidos.csv")
    libres = dict(codigos)
    for nombre, doc in pendientes:
        fac, prov = doc["factura"], doc.get("proveedor") or {}
        numero = " ".join(str(fac.get("numero") or "").split())
        total = Decimal(str(fac.get("total_factura"))).quantize(Decimal("0.01"))
        casa = [n for n, f in libres.items()
                if f.get("E", ("",) * 3)[2] == numero[-12:] and f.get("C", ("",) * 3)[1] == fac.get("fecha")
                and num(f, "Y") == total]
        if not casa:
            falla(f"{nombre}: no está en revisar.csv y ningún registro del IVS tiene su número, fecha y total ({total})")
            continue
        f = libres.pop(casa[0])
        bs = f.get("BS", ("",) * 3)[2]
        if f.get("G", ("",) * 3)[2] != nif_esperado(prov.get("cif_nif"), bs):
            falla(f"{nombre}: el NIF del IVS ({f.get('G', ('',) * 3)[2]}) no es el del proveedor")
        base, cuota = sum((num(f, c) for c in "JKL"), CERO), sum((num(f, c) for c in "STU"), CERO)
        if base != cent(fac.get("base_total")) or cuota != cent(fac.get("iva_total")):
            falla(f"{nombre}: el IVS lleva base {base} e IVA {cuota} y la cabecera {cent(fac.get('base_total'))} "
                  f"y {cent(fac.get('iva_total'))}: tiene que cuadrar exacto")
        if len(numero) > 12 and numero not in f.get("BV", ("",) * 3)[2]:
            falla(f"{nombre}: número recortado sin el completo en BV")
    for n in libres:
        falla(f"IVS código {n}: no corresponde a ninguna factura de las entradas")

    for x in fallos:
        print("  ✗", x)
    estado = "FALLO" if fallos else "OK"
    print(f"VIA_B={estado} ivs={len(ivs)} apuntes={n_apuntes} revisar={len(revisar)} omitidos={len(omitidos)} "
          f"fallos={len(fallos)}")
    return 1 if fallos else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
