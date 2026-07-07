"""
CAPA 1 - Extractor genérico de PDF (cualquier documento).
No interpreta nada: extrae fielmente texto, tablas e inventario de imágenes,
limpia ruido estructural genérico y marca páginas problemáticas.

Salida: extraccion/<nombre>.json

Uso:
    python extractor_generico.py "documentos_originales/archivo.pdf"

Todo corre LOCAL. Ningún dato sale de esta máquina.
"""
import sys
import json
import re
from collections import Counter
from pathlib import Path

import fitz  # PyMuPDF
import pdfplumber

# ---------- Umbrales genéricos ----------
MIN_COLUMNAS_TABLA = 2
MIN_FILAS_TABLA = 2
MIN_CELDAS_LLENAS = 0.25       # proporción mínima de celdas con contenido
UMBRAL_ENCABEZADO = 0.5        # línea presente en >50% de páginas = membrete
MIN_PALABRAS_PAGINA = 5        # menos que esto = página sospechosa (escaneo/imagen)


def limpiar_celda(celda):
    if celda is None:
        return ""
    return " ".join(str(celda).split())


def es_tabla_valida(datos):
    """Filtro genérico de tablas fantasma."""
    if not datos or len(datos) < MIN_FILAS_TABLA:
        return False
    num_cols = max(len(f) for f in datos)
    if num_cols < MIN_COLUMNAS_TABLA:
        return False
    total = sum(len(f) for f in datos)
    llenas = sum(1 for f in datos for c in f if c is not None and str(c).strip())
    return (llenas / total) >= MIN_CELDAS_LLENAS


def detectar_lineas_repetidas(paginas_texto):
    """
    Encabezados/pies genéricos: líneas (normalizadas, sin números de página)
    que aparecen en más del UMBRAL_ENCABEZADO de las páginas.
    """
    contador = Counter()
    n_paginas = len(paginas_texto)
    for texto in paginas_texto:
        vistas_en_pagina = set()
        for linea in texto.split("\n"):
            norm = re.sub(r"\d+", "#", " ".join(linea.split())).strip()
            if len(norm) >= 4 and norm not in vistas_en_pagina:
                vistas_en_pagina.add(norm)
        contador.update(vistas_en_pagina)
    if n_paginas < 3:
        return set()  # con pocas páginas no hay base estadística
    return {l for l, c in contador.items() if c / n_paginas > UMBRAL_ENCABEZADO}


def remover_lineas_repetidas(texto, repetidas):
    lineas_limpias = []
    for linea in texto.split("\n"):
        norm = re.sub(r"\d+", "#", " ".join(linea.split())).strip()
        if norm in repetidas:
            continue
        lineas_limpias.append(linea)
    return "\n".join(lineas_limpias)


def texto_parece_desordenado(texto):
    """
    Heurísticas genéricas de texto mal extraído:
    - alta proporción de líneas de 1-2 caracteres (texto fragmentado)
    - presencia de palabras comunes invertidas (texto rotado)
    """
    lineas = [l.strip() for l in texto.split("\n") if l.strip()]
    if not lineas:
        return False
    cortas = sum(1 for l in lineas if len(l) <= 2)
    if cortas / len(lineas) > 0.4:
        return True
    # Palabras frecuentes en español invertidas = señal de texto rotado
    invertidas = ["led", "noc", "arap", "etnaralced", "nóicanimoned", "nóicces"]
    texto_lower = texto.lower()
    return sum(1 for w in invertidas if w in texto_lower) >= 2


def extraer(ruta_pdf):
    ruta = Path(ruta_pdf)
    resultado = {
        "archivo_origen": ruta.name,
        "paginas": [],
        "tablas": [],
        "paginas_problematicas": [],
        "lineas_membrete_removidas": [],
    }

    # ---------- Pasada 1: texto crudo por página (PyMuPDF) ----------
    doc = fitz.open(ruta)
    resultado["total_paginas"] = len(doc)
    textos_crudos = []
    info_paginas = []
    for i, pagina in enumerate(doc, start=1):
        texto = pagina.get_text("text")
        textos_crudos.append(texto)
        info_paginas.append({
            "numero": i,
            "rotacion": pagina.rotation,
            "imagenes_incrustadas": len(pagina.get_images()),
        })
    doc.close()

    # ---------- Detección genérica de membretes ----------
    repetidas = detectar_lineas_repetidas(textos_crudos)
    resultado["lineas_membrete_removidas"] = sorted(repetidas)

    # ---------- Construcción de páginas limpias + marcado ----------
    for info, texto_crudo in zip(info_paginas, textos_crudos):
        texto_limpio = remover_lineas_repetidas(texto_crudo, repetidas).strip()
        palabras = len(texto_limpio.split())
        problemas = []
        if palabras < MIN_PALABRAS_PAGINA:
            problemas.append("sin_texto_o_escaneada")
        if info["rotacion"] != 0:
            problemas.append(f"pagina_rotada_{info['rotacion']}")
        if texto_parece_desordenado(texto_limpio):
            problemas.append("texto_posiblemente_desordenado")

        resultado["paginas"].append({
            "numero": info["numero"],
            "texto": texto_limpio,
            "palabras": palabras,
            "imagenes_incrustadas": info["imagenes_incrustadas"],
            "problemas": problemas,
        })
        if problemas:
            resultado["paginas_problematicas"].append({
                "pagina": info["numero"],
                "problemas": problemas,
            })

    # ---------- Pasada 2: tablas (pdfplumber) ----------
    with pdfplumber.open(ruta) as pdf:
        for i, pagina in enumerate(pdf.pages, start=1):
            for t in pagina.find_tables():
                datos = t.extract()
                if not es_tabla_valida(datos):
                    continue
                resultado["tablas"].append({
                    "pagina": i,
                    "filas": len(datos),
                    "columnas": max(len(f) for f in datos),
                    "datos": [[limpiar_celda(c) for c in f] for f in datos],
                })

    return resultado


if __name__ == "__main__":
    ruta_pdf = sys.argv[1]
    salida = extraer(ruta_pdf)

    nombre_base = Path(ruta_pdf).stem
    ruta_json = Path("extraccion") / f"{nombre_base}.json"
    ruta_json.parent.mkdir(exist_ok=True)
    with open(ruta_json, "w", encoding="utf-8") as f:
        json.dump(salida, f, ensure_ascii=False, indent=2)

    # ---------- Resumen ----------
    print(f"Archivo: {salida['archivo_origen']}")
    print(f"Páginas: {salida['total_paginas']}")
    print(f"Tablas válidas: {len(salida['tablas'])}")
    print(f"Líneas de membrete removidas: {len(salida['lineas_membrete_removidas'])}")
    for l in salida["lineas_membrete_removidas"]:
        print(f"   - {l}")
    if salida["paginas_problematicas"]:
        print("\nPÁGINAS PROBLEMÁTICAS (candidatas a revisión manual o visión):")
        for p in salida["paginas_problematicas"]:
            print(f"   Pág {p['pagina']}: {', '.join(p['problemas'])}")
    else:
        print("\nSin páginas problemáticas detectadas.")
    print(f"\nGuardado en: {ruta_json}")
