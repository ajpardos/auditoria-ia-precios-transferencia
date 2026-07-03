"""
Extractor de Informe Local (documentación comprobatoria de precios de transferencia).
Produce: texto por página + tablas limpias en JSON.
Todo corre LOCAL - ningún dato sale de esta máquina.
"""
import sys
import json
from pathlib import Path
import fitz  # PyMuPDF
import pdfplumber

# ---------- Configuración ----------
MIN_COLUMNAS = 2      # tablas de 1 columna = fantasmas
MIN_FILAS = 2         # tablas de 1 fila = ruido
MIN_CELDAS_LLENAS = 0.3  # al menos 30% de celdas con contenido

def es_tabla_valida(datos):
    """Filtra las tablas fantasma que genera pdfplumber."""
    if not datos or len(datos) < MIN_FILAS:
        return False
    num_cols = max(len(fila) for fila in datos)
    if num_cols < MIN_COLUMNAS:
        return False
    total_celdas = sum(len(fila) for fila in datos)
    celdas_llenas = sum(
        1 for fila in datos for c in fila
        if c is not None and str(c).strip() != ""
    )
    return (celdas_llenas / total_celdas) >= MIN_CELDAS_LLENAS

def limpiar_celda(celda):
    if celda is None:
        return ""
    return " ".join(str(celda).split())  # colapsa saltos de línea y espacios

def extraer(ruta_pdf):
    ruta = Path(ruta_pdf)
    resultado = {
        "archivo_origen": ruta.name,
        "paginas": [],
        "tablas": [],
    }

    # ---------- Texto con PyMuPDF ----------
    doc = fitz.open(ruta)
    resultado["total_paginas"] = len(doc)
    for i, pagina in enumerate(doc, start=1):
        texto = pagina.get_text("text")
        resultado["paginas"].append({
            "numero": i,
            "texto": texto.strip(),
            "palabras": len(texto.split()),
        })
    doc.close()

    # ---------- Tablas con pdfplumber + filtro ----------
    with pdfplumber.open(ruta) as pdf:
        for i, pagina in enumerate(pdf.pages, start=1):
            for t in pagina.find_tables():
                datos = t.extract()
                if not es_tabla_valida(datos):
                    continue  # descarta fantasmas
                datos_limpios = [
                    [limpiar_celda(c) for c in fila] for fila in datos
                ]
                resultado["tablas"].append({
                    "pagina": i,
                    "filas": len(datos_limpios),
                    "columnas": max(len(f) for f in datos_limpios),
                    "datos": datos_limpios,
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

    # Resumen en pantalla
    print(f"Páginas procesadas: {salida['total_paginas']}")
    print(f"Tablas válidas (tras filtro): {len(salida['tablas'])}")
    for t in salida["tablas"]:
        print(f"  Pág {t['pagina']}: {t['filas']}x{t['columnas']}")
    print(f"\nResultado guardado en: {ruta_json}")
