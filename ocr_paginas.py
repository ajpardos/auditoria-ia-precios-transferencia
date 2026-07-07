"""
CAPA 1B - OCR local de páginas problemáticas + validación aritmética.

Toma el PDF original y el JSON de extracción (que ya marca las páginas
problemáticas), aplica OCR (Tesseract, español) SOLO a esas páginas,
valida las cifras con reglas aritméticas y corrobora contra el texto
digital del resto del documento.

Produce: extraccion/<nombre>_ocr.json  (el mismo JSON, completado)

Todo corre LOCAL. Ninguna imagen ni dato sale de esta máquina.

Uso:
    python ocr_paginas.py "documentos_originales/doc.pdf" extraccion/doc.json

Requisitos:
    sudo dnf install -y oracle-epel-release-el9
    sudo dnf install -y tesseract tesseract-langpack-spa
    pip install pytesseract pillow
"""
import io
import json
import re
import sys
from pathlib import Path

import fitz  # PyMuPDF
import pytesseract
from PIL import Image

DPI_RENDER = 300          # resolución de rasterizado (300 = estándar OCR)
CONFIANZA_MINIMA = 60     # promedio tesseract por debajo de esto = página dudosa
MAX_SUMANDOS = 12         # ventana máxima para detectar subtotales


# ------------------------------------------------------------
# OCR
# ------------------------------------------------------------

def ocr_pagina(doc, numero_pagina):
    """Rasteriza la página con PyMuPDF y aplica Tesseract (spa)."""
    pagina = doc[numero_pagina - 1]
    pix = pagina.get_pixmap(dpi=DPI_RENDER)
    imagen = Image.open(io.BytesIO(pix.tobytes("png")))

    datos = pytesseract.image_to_data(
        imagen, lang="spa", output_type=pytesseract.Output.DICT
    )
    palabras, confianzas = [], []
    for palabra, conf in zip(datos["text"], datos["conf"]):
        palabra = palabra.strip()
        if palabra and conf != -1:
            palabras.append(palabra)
            confianzas.append(int(conf))

    texto = pytesseract.image_to_string(imagen, lang="spa")
    conf_promedio = sum(confianzas) / len(confianzas) if confianzas else 0
    return texto.strip(), round(conf_promedio, 1), len(palabras)


# ------------------------------------------------------------
# Extracción y validación de cifras
# ------------------------------------------------------------

def parsear_numero(token):
    """
    Convierte formatos monetarios colombianos a entero/float.
    '8.543.216' -> 8543216 | '(1.234)' -> -1234 | '1,234.56' se ignora ambiguo
    Devuelve None si no es una cifra confiable.
    """
    t = token.strip().rstrip(".,;")
    negativo = t.startswith("(") and t.endswith(")")
    t = t.strip("()").replace("$", "").strip()
    # Formato col: puntos como miles (sin decimales o coma decimal)
    if re.fullmatch(r"\d{1,3}(\.\d{3})+", t):
        valor = int(t.replace(".", ""))
    elif re.fullmatch(r"\d{1,3}(\.\d{3})+,\d{1,2}", t):
        ent, dec = t.rsplit(",", 1)
        valor = float(ent.replace(".", "") + "." + dec)
    elif re.fullmatch(r"\d{4,}", t):
        valor = int(t)
    else:
        return None
    return -valor if negativo else valor


def extraer_cifras(texto):
    """Todas las cifras 'grandes' (>=4 dígitos efectivos) del texto, en orden."""
    tokens = re.findall(r"\(?\$?\s?\d[\d.,]*\)?", texto)
    cifras = []
    for tk in tokens:
        v = parsear_numero(tk)
        if v is not None and abs(v) >= 1000:
            cifras.append(v)
    return cifras


def validar_subtotales(cifras):
    """
    Heurística genérica de estados financieros: busca cifras que sean la suma
    de un grupo de cifras inmediatamente anteriores (subtotales/totales).
    Devuelve (subtotales_encontrados, detalle).
    """
    encontrados = []
    n = len(cifras)
    for i in range(2, n):
        acumulado = 0
        for k in range(1, min(MAX_SUMANDOS, i) + 1):
            acumulado += cifras[i - k]
            if k >= 2 and acumulado == cifras[i]:
                encontrados.append({
                    "total": cifras[i],
                    "sumandos": cifras[i - k:i],
                })
                break
    return encontrados


def corroborar_con_digital(cifras_ocr, texto_digital):
    """
    ¿Cuántas cifras del OCR aparecen también en las páginas digitales del
    documento (p. ej. el análisis económico citando los estados financieros)?
    Coincidencias = evidencia fuerte de lectura correcta.
    """
    cifras_digital = set(extraer_cifras(texto_digital))
    coincidentes = [c for c in set(cifras_ocr) if c in cifras_digital]
    return coincidentes


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
        cifras = extraer_cifras(texto)
        subtotales = validar_subtotales(cifras)
        corroboradas = corroborar_con_digital(cifras, texto_digital)

        estado = "ok"
        motivos = []
        if confianza < CONFIANZA_MINIMA:
            estado = "dudosa"
            motivos.append(f"confianza OCR baja ({confianza})")
        if n_palabras < 5:
            estado = "vacia_o_ilegible"
            motivos.append("casi sin texto reconocido")
        if cifras and not subtotales and not corroboradas:
            if estado == "ok":
                estado = "cifras_sin_validar"
            motivos.append("cifras presentes pero ninguna validada")

        # Actualizar la página en el JSON
        pagina_json = d["paginas"][num - 1]
        pagina_json["texto"] = texto
        pagina_json["palabras"] = n_palabras
        pagina_json["fuente"] = "ocr_tesseract"
        pagina_json["ocr"] = {
            "confianza_promedio": confianza,
            "cifras_detectadas": len(cifras),
            "subtotales_que_cuadran": len(subtotales),
            "cifras_corroboradas_en_texto_digital": len(corroboradas),
            "estado": estado,
            "motivos": motivos,
        }
        resumen.append((num, confianza, n_palabras, len(cifras),
                        len(subtotales), len(corroboradas), estado))

    doc.close()

    # Recalcular la lista de problemáticas: quedan solo las no resueltas
    d["paginas_problematicas"] = [
        {"pagina": num, "problemas": d["paginas"][num - 1]["ocr"]["motivos"]}
        for num, *_, estado in resumen
        if estado != "ok"
    ]

    ruta_salida = Path(ruta_json).with_name(Path(ruta_json).stem + "_ocr.json")
    with open(ruta_salida, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=2)

    # ---------- Reporte ----------
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
    print("Nota: las páginas OCR quedan marcadas con fuente='ocr_tesseract' y sus")
    print("métricas de validación, para que el Proyecto sepa el nivel de confianza.")


if __name__ == "__main__":
    procesar(sys.argv[1], sys.argv[2])
