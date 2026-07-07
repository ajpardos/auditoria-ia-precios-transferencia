"""
CAPA 2 - Anonimizador genérico con tabla de mapping simétrica.
Toma el JSON de la Capa 1 y produce una versión anónima apta para
enviar al Proyecto/API, más la tabla de mapping (SOLO LOCAL) que
permite la re-identificación de los borradores al final.

Dos fuentes de anonimización:
  1. Patrones universales (automático): NIT, cédulas, emails, teléfonos.
  2. Tabla explícita por caso (entidades.txt): razones sociales, nombres,
     marcas - todo lo que no sigue un patrón universal.

Formato de entidades.txt (una entrada por línea):
    TIPO|texto exacto a reemplazar
    EMPRESA|Trade S.A.S.
    EMPRESA|TRADE SAS
    PERSONA|Juan Pérez Gómez
    EMPRESA_REL|Commercial And Industrial Supplies S.A.

Variantes del mismo nombre (mayúsculas, con/sin puntos) deben listarse
cada una; las variantes del MISMO tipo y orden consecutivo reciben el
mismo código si se marcan con "=" al inicio:
    EMPRESA|Trade S.A.S.
    =EMPRESA|TRADE SAS          <- mismo código que la línea anterior

Uso:
    python anonimizador.py extraccion/archivo.json casos/caso1/entidades.txt

Salidas:
    anonimizado/<nombre>_anon.json     -> apto para enviar
    mapping/<nombre>_mapping.json      -> SOLO LOCAL, NUNCA compartir

Re-identificación (aplicar a borradores que regresan del Proyecto):
    python anonimizador.py --reidentificar borrador.md mapping/<n>_mapping.json
"""
import sys
import json
import re
from pathlib import Path

# ---------- Patrones universales (Colombia) ----------
PATRONES = [
    # NIT con formato: 900.123.456-7 | 900123456-7 | 900,123,456 - 7
    ("NIT", re.compile(r"\b\d{3}[.,]?\d{3}[.,]?\d{3}\s?-\s?\d\b")),
    # Cédulas largas sueltas precedidas de contexto de identificación
    ("CEDULA", re.compile(
        r"(?i)(?:c\.?c\.?|cédula(?:\s+de\s+ciudadanía)?|identificaci[oó]n)\s*(?:no\.?|n[°º])?\s*[:.]?\s*(\d{6,10})"
    )),
    ("EMAIL", re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.]+\b")),
    ("TELEFONO", re.compile(r"(?<!\d)(?:\+57\s?)?3\d{9}(?!\d)")),
]


def cargar_entidades(ruta_entidades):
    """Carga la lista explícita por caso y asigna códigos."""
    entradas = []  # (tipo, texto, codigo)
    contadores = {}
    ultimo_codigo_por_tipo = {}
    with open(ruta_entidades, encoding="utf-8") as f:
        for linea in f:
            linea = linea.strip()
            if not linea or linea.startswith("#"):
                continue
            alias = linea.startswith("=")
            if alias:
                linea = linea[1:]
            if "|" not in linea:
                continue
            tipo, texto = linea.split("|", 1)
            tipo, texto = tipo.strip().upper(), texto.strip()
            if alias and tipo in ultimo_codigo_por_tipo:
                codigo = ultimo_codigo_por_tipo[tipo]
            else:
                contadores[tipo] = contadores.get(tipo, 0) + 1
                codigo = f"{tipo}_{contadores[tipo]:03d}"
                ultimo_codigo_por_tipo[tipo] = codigo
            entradas.append((tipo, texto, codigo))
    # Reemplazar primero los textos MÁS LARGOS evita reemplazos parciales
    entradas.sort(key=lambda e: len(e[1]), reverse=True)
    return entradas


def anonimizar_texto(texto, entidades, mapping):
    """Aplica lista explícita + patrones universales. Registra en mapping."""
    # 1) Lista explícita por caso (case-insensitive, palabra completa donde aplique)
    for tipo, original, codigo in entidades:
        patron = re.compile(re.escape(original), re.IGNORECASE)
        if patron.search(texto):
            mapping.setdefault(codigo, {"tipo": tipo, "valores": []})
            if original not in mapping[codigo]["valores"]:
                mapping[codigo]["valores"].append(original)
            texto = patron.sub(codigo, texto)

    # 2) Patrones universales
    for tipo, patron in PATRONES:
        def reemplazo(m):
            valor = m.group(1) if m.groups() else m.group(0)
            # ¿Ya existe un código para este valor exacto?
            for cod, info in mapping.items():
                if info["tipo"] == tipo and valor in info["valores"]:
                    return m.group(0).replace(valor, cod)
            n = sum(1 for c in mapping.values() if c["tipo"] == tipo) + 1
            codigo = f"{tipo}_{n:03d}"
            mapping[codigo] = {"tipo": tipo, "valores": [valor]}
            return m.group(0).replace(valor, codigo)
        texto = patron.sub(reemplazo, texto)

    return texto


def verificar_residuales(texto):
    """Última línea de defensa: ¿quedó algo con pinta de NIT/identificador?"""
    alertas = []
    for tipo, patron in PATRONES:
        for m in patron.finditer(texto):
            alertas.append(f"Posible {tipo} sin anonimizar: '{m.group(0)[:40]}'")
    return alertas


def anonimizar_json(ruta_json, ruta_entidades):
    with open(ruta_json, encoding="utf-8") as f:
        d = json.load(f)
    entidades = cargar_entidades(ruta_entidades)
    mapping = {}

    # Nombre del archivo origen también puede contener el nombre del contribuyente
    d["archivo_origen"] = "DOCUMENTO_ANONIMIZADO.pdf"

    for p in d.get("paginas", []):
        p["texto"] = anonimizar_texto(p["texto"], entidades, mapping)
    for t in d.get("tablas", []):
        t["datos"] = [
            [anonimizar_texto(c, entidades, mapping) if c else c for c in fila]
            for fila in t["datos"]
        ]
    # Los membretes removidos suelen contener la razón social
    d["lineas_membrete_removidas"] = [
        anonimizar_texto(l, entidades, mapping)
        for l in d.get("lineas_membrete_removidas", [])
    ]

    # ---------- Verificación residual ----------
    residuales = []
    for p in d.get("paginas", []):
        residuales.extend(verificar_residuales(p["texto"]))

    # ---------- Guardar ----------
    nombre = Path(ruta_json).stem
    dir_anon = Path("anonimizado"); dir_anon.mkdir(exist_ok=True)
    dir_map = Path("mapping"); dir_map.mkdir(exist_ok=True)

    ruta_anon = dir_anon / f"{nombre}_anon.json"
    ruta_map = dir_map / f"{nombre}_mapping.json"

    with open(ruta_anon, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=2)
    with open(ruta_map, "w", encoding="utf-8") as f:
        json.dump(mapping, f, ensure_ascii=False, indent=2)

    print(f"Entidades anonimizadas: {len(mapping)}")
    for cod, info in mapping.items():
        print(f"   {cod} <- {len(info['valores'])} variante(s)")
    if residuales:
        print("\n⚠ POSIBLES RESIDUALES - REVISAR ANTES DE ENVIAR:")
        for a in residuales[:20]:
            print(f"   {a}")
    else:
        print("\nVerificación residual: sin patrones sensibles detectados.")
    print(f"\nAnónimo (apto para enviar):  {ruta_anon}")
    print(f"Mapping (SOLO LOCAL):        {ruta_map}")


def reidentificar(ruta_borrador, ruta_mapping):
    """Reemplaza códigos por valores reales en un borrador que regresó."""
    with open(ruta_mapping, encoding="utf-8") as f:
        mapping = json.load(f)
    with open(ruta_borrador, encoding="utf-8") as f:
        texto = f.read()

    for codigo, info in mapping.items():
        # Usa la PRIMERA variante registrada como forma canónica
        texto = texto.replace(codigo, info["valores"][0])

    salida = Path(ruta_borrador).with_name(
        Path(ruta_borrador).stem + "_REIDENTIFICADO" + Path(ruta_borrador).suffix
    )
    with open(salida, "w", encoding="utf-8") as f:
        f.write(texto)
    print(f"Borrador re-identificado: {salida}")
    print("Recuerda: este archivo contiene datos reales. Manejo local únicamente.")


if __name__ == "__main__":
    if sys.argv[1] == "--reidentificar":
        reidentificar(sys.argv[2], sys.argv[3])
    else:
        anonimizar_json(sys.argv[1], sys.argv[2])
