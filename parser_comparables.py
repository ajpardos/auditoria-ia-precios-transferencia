"""
Parser de la matriz de comparables desde el texto extraído.
Patrón: Número → Aceptada/Rechazada → Razón → EMPRESA (mayúsculas) → Descripción
También valida contra la lista de comparables del resumen ejecutivo.
Todo corre LOCAL.
"""
import sys
import json
import re
from pathlib import Path

ESTADOS = {"Aceptada", "Rechazada"}

def es_mayusculas(linea):
    """Línea de nombre de empresa: mayúsculas, permite & . - dígitos."""
    limpia = re.sub(r"[\s&.,\-()0-9]", "", linea)
    return len(limpia) >= 3 and limpia.isupper()

def parsear_matriz(texto_paginas):
    """Recorre las líneas de las páginas de la matriz y arma registros."""
    lineas = []
    for texto in texto_paginas:
        for l in texto.split("\n"):
            l = l.strip()
            if l:
                lineas.append(l)

    registros = []
    i = 0
    while i < len(lineas):
        # Inicio de registro: línea que es solo un número y la siguiente es un estado
        if re.fullmatch(r"\d{1,3}", lineas[i]) and i + 1 < len(lineas) and lineas[i+1] in ESTADOS:
            numero = int(lineas[i])
            estado = lineas[i+1]
            j = i + 2
            razon, empresa, descripcion = [], [], []
            # Razón: hasta encontrar la primera línea en mayúsculas (empresa)
            while j < len(lineas) and not es_mayusculas(lineas[j]):
                razon.append(lineas[j]); j += 1
            # Empresa: líneas consecutivas en mayúsculas
            while j < len(lineas) and es_mayusculas(lineas[j]):
                empresa.append(lineas[j]); j += 1
            # Descripción: hasta el próximo inicio de registro
            while j < len(lineas):
                if re.fullmatch(r"\d{1,3}", lineas[j]) and j + 1 < len(lineas) and lineas[j+1] in ESTADOS:
                    break
                descripcion.append(lineas[j]); j += 1
            registros.append({
                "numero": numero,
                "estado": estado,
                "razon": " ".join(razon),
                "empresa": " ".join(empresa),
                "descripcion": " ".join(descripcion),
            })
            i = j
        else:
            i += 1
    return registros

def extraer_lista_resumen(texto_paginas):
    """Lista numerada del resumen ejecutivo: '1. BELEAF SA' etc."""
    empresas = []
    patron = re.compile(r"^(\d{1,2})\.\s+(.+)$")
    for texto in texto_paginas:
        for l in texto.split("\n"):
            m = patron.match(l.strip())
            if m and es_mayusculas(m.group(2)):
                empresas.append(m.group(2).strip())
    return empresas

if __name__ == "__main__":
    ruta_json = sys.argv[1]
    with open(ruta_json, encoding="utf-8") as f:
        d = json.load(f)

    paginas = {p["numero"]: p["texto"] for p in d["paginas"]}

    # Páginas de la matriz: las que contienen el encabezado o el patrón
    pags_matriz = [n for n, t in paginas.items()
                   if re.search(r"\n(Aceptada|Rechazada)\n", t)]
    # Páginas candidatas del resumen (lista numerada de empresas)
    pags_resumen = [n for n, t in paginas.items()
                    if re.search(r"^\d{1,2}\.\s+[A-Z]", t, re.M) and n < min(pags_matriz, default=999)]

    print(f"Páginas con matriz de comparables: {pags_matriz}")
    print(f"Páginas candidatas de lista resumen: {pags_resumen}\n")

    registros = parsear_matriz([paginas[n] for n in pags_matriz])
    lista_resumen = extraer_lista_resumen([paginas[n] for n in pags_resumen])

    print(f"Comparables parseadas: {len(registros)}")
    aceptadas = [r for r in registros if r["estado"] == "Aceptada"]
    print(f"  Aceptadas: {len(aceptadas)} | Rechazadas: {len(registros)-len(aceptadas)}\n")

    # ---- Validaciones automáticas ----
    alertas = []
    # 1. Numeración continua
    numeros = [r["numero"] for r in registros]
    faltantes = sorted(set(range(min(numeros), max(numeros)+1)) - set(numeros)) if numeros else []
    if faltantes:
        alertas.append(f"Números de comparable faltantes en la matriz: {faltantes}")
    # 2. Estado vs razón inconsistente
    for r in registros:
        if r["estado"] == "Rechazada" and "aceptable" in r["razon"].lower():
            alertas.append(f"#{r['numero']} {r['empresa']}: Rechazada pero razón '{r['razon']}'")
        if r["estado"] == "Aceptada" and "diferente" in r["razon"].lower():
            alertas.append(f"#{r['numero']} {r['empresa']}: Aceptada pero razón '{r['razon']}'")
    # 3. Cruce matriz vs resumen ejecutivo
    aceptadas_nombres = {r["empresa"] for r in aceptadas}
    for e in lista_resumen:
        if e not in aceptadas_nombres:
            alertas.append(f"'{e}' está en la lista del resumen pero NO figura Aceptada en la matriz")
    for e in aceptadas_nombres:
        if lista_resumen and e not in lista_resumen:
            alertas.append(f"'{e}' figura Aceptada en la matriz pero NO está en la lista del resumen")

    if alertas:
        print("=== ALERTAS PARA REVISIÓN HUMANA ===")
        for a in alertas:
            print(f"  ⚠ {a}")
    else:
        print("Sin inconsistencias detectadas entre matriz y resumen.")

    # Guardar resultado
    salida = {
        "comparables": registros,
        "lista_resumen_ejecutivo": lista_resumen,
        "alertas": alertas,
    }
    ruta_salida = Path(ruta_json).with_name(Path(ruta_json).stem + "_comparables.json")
    with open(ruta_salida, "w", encoding="utf-8") as f:
        json.dump(salida, f, ensure_ascii=False, indent=2)
    print(f"\nGuardado en: {ruta_salida}")
