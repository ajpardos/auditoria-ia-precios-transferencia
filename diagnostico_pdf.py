"""Diagnóstico de extracción de PDF: texto, tablas, imágenes, rotación."""
import sys
import fitz  # PyMuPDF
import pdfplumber

ruta = sys.argv[1]

print(f"=== DIAGNÓSTICO: {ruta} ===\n")

# --- PyMuPDF: estructura general ---
doc = fitz.open(ruta)
print(f"Páginas: {len(doc)}")

for i, pagina in enumerate(doc, start=1):
    texto = pagina.get_text()
    imagenes = pagina.get_images()
    rotacion = pagina.rotation
    palabras = len(texto.split())
    print(f"\n--- Página {i} ---")
    print(f"  Rotación de página: {rotacion}°")
    print(f"  Palabras extraídas: {palabras}")
    print(f"  Imágenes incrustadas: {len(imagenes)}")
    # Muestra los primeros 300 caracteres para inspección visual
    muestra = texto[:300].replace("\n", " | ")
    print(f"  Muestra de texto: {muestra}")

doc.close()

# --- pdfplumber: tablas ---
print("\n\n=== DETECCIÓN DE TABLAS (pdfplumber) ===")
with pdfplumber.open(ruta) as pdf:
    for i, pagina in enumerate(pdf.pages, start=1):
        tablas = pagina.find_tables()
        if tablas:
            print(f"\n--- Página {i}: {len(tablas)} tabla(s) detectada(s) ---")
            for j, t in enumerate(tablas, start=1):
                datos = t.extract()
                filas = len(datos)
                cols = len(datos[0]) if datos else 0
                print(f"  Tabla {j}: {filas} filas x {cols} columnas")
                # Muestra las primeras 2 filas
                for fila in datos[:2]:
                    print(f"    {fila}")
