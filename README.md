# munincloud-conector-contasol

**MuninCloud · conector Contasol (programa de destino).** Convierte las respuestas de la **API de auditoría de facturas
de MuninCloud** (formato `v1`) en los dos ficheros que importa Contasol para las facturas **recibidas**: `IVS.ods`
(libro de IVA soportado) y `APU.ods` (los apuntes del diario de cada factura, enlazados con su registro de IVA).

Es un único fichero de Python sin dependencias (`munincloud_contasol.py`, más la pieza común `munincloud_v1.py`).
Cógelo, léelo y adáptalo a tu programa: para eso está. Licencia MIT (`LICENSE`).

## Requisitos

Python 3.8 o posterior (solo biblioteca estándar). Probado con Python 3.12; con 3.8 se ha comprobado la sintaxis. No hace falta LibreOffice ni Excel para generar los `.ods`.

## En cinco minutos

```bash
python3 munincloud_contasol.py --mi-nif B00000018 --salida salida --maestro ejemplos/proveedores.csv --libro-iva 1 --diario 1 --cuenta-iva 472 --cuenta-gasto 629 --cuenta-retencion 4751 ejemplos/entrada
head -3 salida/revisar.csv
python3 pruebas/via_b.py salida ejemplos/entrada
python3 -m unittest discover -s pruebas
```

`B00000018` es un NIF reservado por la AEAT para pruebas: pon el tuyo. La salida tiene que ser la de `ejemplos/salida/`.

Lo que sale en `salida/`:

| fichero | qué |
|---|---|
| `IVS.ods` | libro de IVA soportado: una fila por factura, columnas A..BV, **sin fila de cabecera** (en el importador, fila inicial 1) |
| `APU.ods` | el asiento de cada factura: gasto y IVA al debe, retención y proveedor al haber; `APU.N` = `IVS.A`. Solo si das el diario y las cuentas |
| `revisar.csv` | lo que NO va en los `.ods`, con sus motivos |
| `omitidos.csv` | lo que no es una factura (trabajos sin terminar, rechazados) |
| `avisos.csv` | lo que entra, pero hay que saber (un número recortado, el libro de IVA supuesto, el asiento que no se genera) |

En Contasol: Utilidades › Importaciones › Archivos `.XLSX / .XLS`, la carpeta de `salida/` y fila inicial 1.

Opciones:

| opción | qué | si no la das |
|---|---|---|
| `--maestro` | fichero `NIF;subcuenta` de tus proveedores (la subcuenta va en `IVS.D` y en el apunte del proveedor) | la factura sale a revisar (`CONTASOL_SIN_CUENTA_PROVEEDOR`) salvo que des `--cuenta-proveedor` |
| `--digitos` | dígitos de las cuentas auxiliares de tu empresa (1-10) | 8. Las cuentas cortas se completan con ceros (`472` → `47200000`); una subcuenta del maestro con otra longitud es un error |
| `--libro-iva` | número de libro de IVA soportado (`IVS.B`) | 1, con aviso `CONTASOL_LIBRO_IVA_SUPUESTO` |
| `--diario`, `--cuenta-iva`, `--cuenta-gasto`, `--cuenta-retencion` | el diario de compras, la 472, la cuenta de gasto y la 4751 | no hay `APU.ods` (`CONTASOL_SIN_ASIENTO`); el IVS se importa igual. La 4751 solo hace falta si el lote lleva retenciones |
| `--clave-extranjero` | la clave de un proveedor no español sin IVA cuando el concepto no deja saber si es bien o servicio | la factura sale a revisar (`CONTASOL_CLAVE_NO_DEDUCIBLE`) |
| `--primer-codigo` | primer código de `IVS.A` (es índice: no puede repetir uno que ya tengas en Contasol) | 1 |

## Compra o venta: cómo lo decide

Manda la `direccion` de la respuesta (`RECIBIDA` o `EMITIDA`); si no la trae, tu NIF (`--mi-nif`) contra el emisor y el
receptor, y `--direccion` la fuerza para todo el lote. **Este conector solo escribe RECIBIDAS.** Las emitidas salen a
revisar con `CONTASOL_VENTA_SIN_LIBRO`: el libro de IVA repercutido (`IVR`) de Contasol no está en la documentación
pública, y escribir una venta en el `IVS` sería contabilizarla como una compra sin que nada diera error.

## Cuándo una factura sale para revisar

Una factura que sale a revisar **no va** en `IVS.ods` ni en `APU.ods`: va en `revisar.csv`, con sus motivos.

| motivo | qué pasa |
|---|---|
| los de la pieza común (`DESCUADRE`, `AVISO_*`, `DIRECCION_DESCONOCIDA`, `NIF_AEAT_*`, `DIVISA_NO_EUR`, `NUMERO_NO_LEIDO`, …) | la lectura no se puede contabilizar tal cual: ver la cabecera de `munincloud_v1.py` |
| `CONTASOL_VENTA_SIN_LIBRO` | es una emitida (arriba) |
| `CONTASOL_RECTIFICATIVA` | es una rectificativa: **siempre** a revisar. El bloque de la factura rectificada del IVS (`AA`-`AR`) no tiene las posiciones publicadas y sin él la rectificativa podría restar mal en el modelo 303; contabilízala a mano, con la factura que rectifica a la vista |
| `CONTASOL_DEMASIADOS_TIPOS` | más de 3 tipos de IVA: el libro de Contasol guarda 3, y con los tres mayores el asiento no cuadraría con el total |
| `CONTASOL_SIN_CUENTA_PROVEEDOR` | el NIF no está en el maestro y no diste `--cuenta-proveedor` |
| `CONTASOL_CLAVE_NO_DEDUCIBLE` | proveedor no español sin IVA español y no se sabe si es bien o servicio |
| `CONTASOL_NIF_DEMASIADO_LARGO` | el NIF pasa de 12 caracteres (un NIF recortado es otro NIF) |
| `CONTASOL_CAMPO_DEMASIADO_LARGO` | un importe no cabe en su columna (15 caracteres) |
| `CONTASOL_ASIENTO_NO_CUADRA` | debe y haber difieren (por ejemplo, un céntimo que la pieza tolera como redondeo del papel) |
| `CONTASOL_CODIGO_AGOTADO` | `IVS.A` llega a 99999 |

Y en `avisos.csv`, lo que entra pero hay que saber: `CONTASOL_NUMERO_RECORTADO` (el número pasa de 12: va recortado
**por la cola** y entero en `BV`, observaciones) y `CONTASOL_CAMPO_RECORTADO` (el nombre pasa de 100). Contasol
**descarta la fila entera sin avisar** si un texto pasa de su máximo: por eso aquí ninguno pasa.

El conector **no arregla** nada de esto: lo enseña. Una factura que no cuadra sale con su descuadre a la vista.

**Duplicadas y redondeo** (pieza común 1.3.0). Si el lote trae la misma factura dos veces (misma dirección, NIF de la otra parte, número —solo letras y cifras— y fecha), la primera sigue su camino y la segunda y siguientes salen a revisar con `POSIBLE_DUPLICADA`: nunca se funden. Si las líneas difieren de la cabecera en 1 o 2 céntimos (de base, de IVA o de los dos), es redondeo del papel: se funde en la línea y el registro de IVA de mayor base, lo importable cuadra **exacto** con la cabecera y la factura no se aparta por eso; queda el aviso `AJUSTE_REDONDEO` en `avisos.csv` (la factura entra igual). Con 3 céntimos o más, `DESGLOSE_NO_CUADRA` y a revisar, como siempre.

## Lo que la respuesta `v1` no trae, y este conector no inventa

| dato | dónde va | lo pones tú |
|---|---|---|
| la subcuenta del proveedor | `IVS.D`, `APU.E` | `--maestro` (o `--cuenta-proveedor`) |
| el libro de IVA, el diario, la 472, la cuenta de gasto, la 4751 | `IVS.B`, `APU.A`, `APU.E` | sus opciones; una sola cuenta de gasto para todo el lote |
| bien o servicio de un proveedor extranjero | `IVS.H`, `IVS.BB` | lo deduce `clave_operacion()` de la pieza común por el concepto; si no puede, `--clave-extranjero` |
| la inversión del sujeto pasivo nacional | `IVS.BB` = 8 | `v1` no la trae: la clave 8 solo sale de la ayuda `clave_operacion()` (servicios de fuera) o de `--clave-extranjero` |
| el tipo de retención (profesional, arrendamiento…) | `IVS.AV` | vacío: un 15 % lo mismo es un profesional que otra cosa |
| el bloque de la rectificada | `IVS.AA`-`AR` | la documentación pública no da la posición de cada campo: por eso las rectificativas no se escriben en el IVS ni en el APU y salen a revisar (`CONTASOL_RECTIFICATIVA`) |
| el 347, bienes de inversión, prorrata, fecha de operación | `AS`, `BE`-`BK`, `AZ`, `CB` | vacíos: Contasol toma sus valores por defecto |

`H` (tipo de operación) es 2 para un proveedor de la UE y 1 para una importación de bienes; `BC` es 2 (NIF-IVA
intracomunitario) para la UE, y entonces el NIF va **sin** su prefijo de país en `G` y el país en `BS`
(`FR40123456824` → `G` = `40123456824`, `BS` = `FR`: con el prefijo no cabe en 12). La clave `BB`, con esta precedencia:
17 adquisición intracomunitaria de bienes · 8 inversión del sujeto pasivo · 3 varios tipos · 0 habitual. La clave 4
(rectificativa) no se usa: las rectificativas van a revisar.

## Contra qué se ha verificado

- **2026-10-04** · contra la transcripción de la especificación pública de DELSOL (ed. 2018) del expediente y el banco
  común; no importado en Contasol. Los `.ods` se han leído con un lector propio (`pruebas/via_b.py`, `zipfile` +
  `xml.etree`, sin código del conector): `mimetype` primero y sin comprimir, cada celda dentro de su máximo, cada
  registro cuadra, cada `APU.N` existe en `IVS.A` y cada asiento cuadra. Y que ninguna rectificativa llega al IVS
  ni al APU: todas están en `revisar.csv` y ningún registro lleva la clave 4.

Lo que no figura aquí **no está verificado** en el programa: la salida cumple el formato documentado, nada más. En
particular, no se ha comprobado si Contasol lee el valor de la celda o el texto que se ve, si crea la subcuenta del
proveedor cuando no existe. Las rectificativas no se escriben en el IVS (salen a revisar), así que tampoco se ha
comprobado cómo las trataría Contasol.

## Pruebas

```bash
python3 -m unittest discover -s pruebas -v
```

Comprueban, sobre los ejemplos y sobre las respuestas desafinadas de `pruebas/datos/`, que cada factura cuadra o sale
para revisar y que la salida es la de `ejemplos/salida/`. Y que `pruebas/via_b.py` se pone en rojo si en un `.ods` se
cambia un importe, se borra una fila o un apunte, un texto pasa de su máximo, `mimetype` no va el primero o una
rectificativa aparece en el IVS.

`munincloud_v1.py` es la misma pieza en todos los conectores de MuninCloud: si la cambias, cámbiala en todos.

## Marcas

Contasol® es una marca de Software DELSOL. MuninCloud no está asociado a, ni certificado por, Software DELSOL.

## Licencia

MIT. Copyright (c) 2026 Bogarra Integrated SL. Ver `LICENSE`.
