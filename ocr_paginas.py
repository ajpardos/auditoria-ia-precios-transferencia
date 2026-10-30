"""
CAPA 1B v2 - OCR local de páginas problemáticas + validación aritmética afinada.

Mejoras v2 del validador:
  1. Parser tolerante a ruido OCR: '380,828,.916' -> 380828916,
     mezcla de puntos/comas, dobles separadores, '$', paréntesis negativos.
  2. Separación de columnas: los estados financieros traen varias columnas
     (2022 | 2021). Los números se agrupan por posición en la línea y cada
     columna se valida por separado.
  3. Soporte de restas: un subtotal puede incluir un componente negativo
     (ej. depreciación acumulada). Se prueban variantes con un sumando negado.
  4. Se excluyen valores con pinta de año (1900-2100) para no contaminar.

Uso:
    python ocr_paginas.py "documentos_originales/doc.pdf" extraccion/doc.json

Salida: extraccion/<nombre>_ocr.json
Todo corre LOCAL.
"""
import io
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

import fitz  # PyMuPDF
import pytesseract
from PIL import Image

DPI_RENDER = 300
CONFIANZA_MINIMA = 60
MAX_SUMANDOS = 10
MAX_SUMANDOS_CON_RESTA = 6   # ventanas con un término negado (más costoso)


# ------------------------------------------------------------
# OCR
# ------------------------------------------------------------

def ocr_pagina(doc, numero_pagina):
    pagina = doc[numero_pagina - 1]
    pix = pagina.get_pixmap(dpi=DPI_RENDER)
    imagen = Image.open(io.BytesIO(pix.tobytes("png")))

    datos = pytesseract.image_to_data(
        imagen, lang="spa", output_type=pytesseract.Output.DICT
    )
    confianzas = [
        int(c) for t, c in zip(datos["text"], datos["conf"])
        if t.strip() and c != -1
    ]
    texto = pytesseract.image_to_string(imagen, lang="spa")
    conf_prom = round(sum(confianzas) / len(confianzas), 1) if confianzas else 0
    return texto.strip(), conf_prom, len(confianzas)


# ------------------------------------------------------------
# Parser de cifras tolerante a ruido OCR
# ------------------------------------------------------------

TOKEN_CIFRA = re.compile(r"\(?-?\$?\d[\d.,]*\d\)?")

def parsear_numero(token):
    """
    Normaliza formatos monetarios con ruido OCR:
      '380,828,.916' -> 380828916   '(1.234)' -> -1234
      '89.484.184'   -> 89484184    '-34,363.041' -> -34363041
    Estrategia: si hay separadores mixtos o agrupación inválida, se
    eliminan TODOS los separadores (los estados financieros colombianos
    rara vez usan decimales; y ',dd' final se trata como decimal).
    """
    t = token.strip()
    negativo = ("(" in t and ")" in t) or t.lstrip().startswith("-")
    t = re.sub(r"[()\s$-]", "", t)
    if not t or not re.search(r"\d", t):
        return None

    # Decimal claro: coma + 1-2 dígitos al final
    decimal = None
    m = re.fullmatch(r"(.+),(\d{1,2})", t)
    if m and "." in m.group(1):
        t, decimal = m.group(1), m.group(2)

    digitos = re.sub(r"[.,]", "", t)
    if not digitos.isdigit():
        return None
    valor = int(digitos)
    if decimal:
        valor = float(f"{valor}.{decimal}")

    # Excluir años y valores triviales
    if isinstance(valor, int) and 1900 <= valor <= 2100:
        return None
    if abs(valor) < 1000:
        return None
    return -valor if negativo else valor


def cifras_por_linea(texto):
    """
    Devuelve lista de (linea, [cifras]) y además las secuencias por columna:
    el número j-ésimo de cada línea alimenta la columna j.
    """
    lineas_cifras = []
    columnas = defaultdict(list)
    for linea in texto.split("\n"):
        nums = []
        for tk in TOKEN_CIFRA.findall(linea):
            v = parsear_numero(tk)
            if v is not None:
                nums.append(v)
        if nums:
            lineas_cifras.append((linea.strip(), nums))
            for j, v in enumerate(nums):
                columnas[j].append(v)
    return lineas_cifras, columnas


# ------------------------------------------------------------
# Validación de subtotales (por columna, con restas)
# ------------------------------------------------------------

def es_trivial(sumandos):
    """Identidad trivial: un sumando negado cancela exactamente a otro
    (aparece con líneas duplicadas tipo total repetido). No valida nada."""
    positivos = [v for v in sumandos if v > 0]
    return any(-v in positivos for v in sumandos if v < 0)


def validar_subtotales_secuencia(cifras):
    """
    Busca cifras que sean la suma de las k anteriores (k>=2), permitiendo
    además variantes donde UNO de los sumandos entra restando
    (caso típico: depreciación acumulada sin signo en el OCR).
    """
    encontrados = []
    n = len(cifras)
    for i in range(2, n):
        # 1) Suma directa
        acumulado = 0
        hallado = False
        for k in range(1, min(MAX_SUMANDOS, i) + 1):
            acumulado += cifras[i - k]
            if k >= 2 and acumulado == cifras[i]:
                encontrados.append({
                    "total": cifras[i],
                    "sumandos": cifras[i - k:i],
                    "modo": "suma",
                })
                hallado = True
                break
        if hallado:
            continue
        # 2) Suma con un término negado
        for k in range(2, min(MAX_SUMANDOS_CON_RESTA, i) + 1):
            ventana = cifras[i - k:i]
            suma = sum(ventana)
            for idx, v in enumerate(ventana):
                if suma - 2 * v == cifras[i]:
                    sumandos = list(ventana)
                    sumandos[idx] = -v
                    if es_trivial(sumandos):
                        continue  # cancelación exacta: no valida nada
                    encontrados.append({
                        "total": cifras[i],
                        "sumandos": sumandos,
                        "modo": "suma_con_resta",
                    })
                    hallado = True
                    break
            if hallado:
                break
    return encontrados


def validar_pagina(texto):
    """Valida por columnas y devuelve métricas + detalle."""
    _, columnas = cifras_por_linea(texto)
    subtotales = []
    for j, secuencia in sorted(columnas.items()):
        for s in validar_subtotales_secuencia(secuencia):
            s["columna"] = j + 1
            subtotales.append(s)
    total_cifras = sum(len(v) for v in columnas.values())
    return total_cifras, subtotales


def corroborar_con_digital(texto_ocr, texto_digital):
    _, cols_ocr = cifras_por_linea(texto_ocr)
    _, cols_dig = cifras_por_linea(texto_digital)
    set_ocr = {v for seq in cols_ocr.values() for v in seq}
    set_dig = {v for seq in cols_dig.values() for v in seq}
    return sorted(set_ocr & set_dig)


# ------------------------------------------------------------
# Principal
# ------------------------------------------------------------

def procesar(ruta_pdf, ruta_json):
    with open(ruta_json, encoding="utf-8") as f:
        d = json.load(f)

    problematicas = [p["pagina"] for p in d.get("paginas_problematicas", [])]
    if not problematicas:
        print("No hay páginas problemáticas marcadas. Nada que hacer.")
        return

    texto_digital = "\n".join(
        p["texto"] for p in d["paginas"] if not p.get("problemas")
    )

    doc = fitz.open(ruta_pdf)
    resumen = []

    for num in problematicas:
        texto, confianza, n_palabras = ocr_pagina(doc, num)
        n_cifras, subtotales = validar_pagina(texto)
        corroboradas = corroborar_con_digital(texto, texto_digital)

        estado = "ok"
        motivos = []
        if confianza < CONFIANZA_MINIMA:
            estado = "dudosa"
            motivos.append(f"confianza OCR baja ({confianza})")
        if n_palabras < 5:
            estado = "vacia_o_ilegible"
            motivos.append("casi sin texto reconocido")
        if n_cifras >= 10 and not subtotales and not corroboradas:
            if estado == "ok":
                estado = "cifras_sin_validar"
            motivos.append("página densa en cifras sin ninguna validación")

        pagina_json = d["paginas"][num - 1]
        pagina_json["texto"] = texto
        pagina_json["palabras"] = n_palabras
        pagina_json["fuente"] = "ocr_tesseract"
        pagina_json["ocr"] = {
            "confianza_promedio": confianza,
            "cifras_detectadas": n_cifras,
            "subtotales_que_cuadran": len(subtotales),
            "detalle_subtotales": subtotales[:15],
            "cifras_corroboradas_en_texto_digital": len(corroboradas),
            "estado": estado,
            "motivos": motivos,
        }
        resumen.append((num, confianza, n_palabras, n_cifras,
                        len(subtotales), len(corroboradas), estado))

    doc.close()

    d["paginas_problematicas"] = [
        {"pagina": num, "problemas": d["paginas"][num - 1]["ocr"]["motivos"]}
        for num, *_, estado in resumen
        if estado != "ok"
    ]

    ruta_salida = Path(ruta_json).with_name(Path(ruta_json).stem + "_ocr.json")
    with open(ruta_salida, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=2)

    print(f"{'Pág':>4} {'Conf':>6} {'Palab':>6} {'Cifras':>7} {'Subtot✓':>8} {'Corrob✓':>8}  Estado")
    for num, conf, pal, cif, sub, cor, estado in resumen:
        print(f"{num:>4} {conf:>6} {pal:>6} {cif:>7} {sub:>8} {cor:>8}  {estado}")

    pendientes = [r for r in resumen if r[-1] != "ok"]
    print(f"\nPáginas resueltas por OCR: {len(resumen) - len(pendientes)}/{len(resumen)}")
    if pendientes:
        print("Pendientes (candidatas a visión o revisión manual):")
        for num, *_, estado in pendientes:
            print(f"   Pág {num}: {estado}")
    print(f"\nGuardado en: {ruta_salida}")


if __name__ == "__main__":
    procesar(sys.argv[1], sys.argv[2])
