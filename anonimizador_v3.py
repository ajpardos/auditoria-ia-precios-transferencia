"""
CAPA 2 - Anonimizador genérico v2, con tabla de mapping simétrica.

Novedades v2:
  - Verificación de NÚCLEOS: tras anonimizar, busca variantes sobrevivientes
    de las entidades listadas (aunque difieran en sufijos/puntuación) y alerta.
  - Modo --sugerir: escanea una extracción y propone candidatos a entidades
    para construir el entidades.txt (el humano decide, el script propone).

Modos de uso:
  1) Sugerir entidades para un caso nuevo:
       python anonimizador.py --sugerir extraccion/archivo.json
  2) Anonimizar:
       python anonimizador.py extraccion/archivo.json casos/caso1/entidades.txt
  3) Re-identificar un borrador que regresa del Proyecto:
       python anonimizador.py --reidentificar borrador.md mapping/archivo_mapping.json

Formato de entidades.txt (una entrada por línea):
    TIPO|texto exacto a reemplazar
    EMPRESA|Trade S.A.S.
    =EMPRESA|Trade S.A.S          <- '=' : mismo código que la línea anterior
    GRUPO|Cordillera Products
    PERSONA|Juan Pérez Gómez

Salidas de anonimización:
    anonimizado/<n>_anon.json   -> apto para enviar
    mapping/<n>_mapping.json    -> SOLO LOCAL, NUNCA compartir
"""
import sys
import json
import re
from collections import Counter
from pathlib import Path

# ---------- Patrones universales (Colombia) ----------
PATRONES = [
    ("NIT", re.compile(r"\b\d{3}[.,]?\d{3}[.,]?\d{3}\s?-\s?\d\b")),
    ("CEDULA", re.compile(
        r"(?i)(?:c\.?c\.?|cédula(?:\s+de\s+ciudadanía)?|identificaci[oó]n)\s*(?:no\.?|n[°º])?\s*[:.]?\s*(\d{6,10})"
    )),
    ("EMAIL", re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.]+\b")),
    ("TELEFONO", re.compile(r"(?<!\d)(?:\+57\s?)?3\d{9}(?!\d)")),
]

# Palabras que no cuentan como "núcleo" identificador
STOPWORDS_NUCLEO = {
    "s.a.s", "sas", "s.a", "sa", "ltda", "limitada", "s.a.s.", "s.a.",
    "inc", "corp", "co", "ltd", "llc", "plc", "de", "del", "la", "el",
    "los", "las", "y", "and", "the", "group", "grupo", "company",
}

# Sufijos societarios para el modo --sugerir
SUFIJOS_SOCIETARIOS = re.compile(
    r"\b(S\.?A\.?S\.?|LTDA\.?|S\.?A\.?|LIMITADA|E\.?U\.?|S\.?C\.?A\.?)\b", re.IGNORECASE
)

# Contextos que suelen preceder/acompañar al contribuyente o su grupo
CONTEXTOS_IDENTIDAD = [
    r"raz[oó]n social[:\s]+([A-ZÁÉÍÓÚÑ][\w.&\-\s]{3,60})",
    r"forma parte (?:de la organizaci[oó]n(?: regional)?|del grupo) (?:de )?([A-ZÁÉÍÓÚÑ][\w.&\-\s]{3,60})",
    r"(?:su )?(?:matriz|controlante|casa matriz)[,:\s]+([A-ZÁÉÍÓÚÑ][\w.&\-\s]{3,60})",
    r"subordinada[s]? de ([A-ZÁÉÍÓÚÑ][\w.&\-\s]{3,60})",
    r"representante legal[:\s]+([A-ZÁÉÍÓÚÑ][a-záéíóúñ]+(?:\s+[A-ZÁÉÍÓÚÑ][a-záéíóúñ]+){1,3})",
]


# ============================================================
# Utilidades comunes
# ============================================================

def nucleo_de(texto):
    """Palabras significativas de un nombre (sin sufijos societarios ni cortas)."""
    palabras = re.findall(r"[\wÁÉÍÓÚÑáéíóúñ.&\-]+", texto)
    return [
        p for p in palabras
        if p.lower().strip(".") not in STOPWORDS_NUCLEO and len(p.strip(".")) >= 3
    ]


def cargar_entidades(ruta_entidades):
    entradas = []
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
    entradas.sort(key=lambda e: len(e[1]), reverse=True)
    return entradas


# ============================================================
# Anonimización
# ============================================================

def patron_entidad(original):
    """
    Patrón robusto para una entidad:
      - los espacios del nombre toleran cualquier blanco (incluye saltos de línea)
      - fronteras de palabra en los extremos (si aplican) para no partir palabras
        ajenas ('Trade' no debe tocar 'trading')
    """
    partes = [re.escape(p) for p in original.split()]
    cuerpo = r"[\s]+".join(partes)
    inicio = r"\b" if original[:1].isalnum() else ""
    fin = r"\b" if original[-1:].isalnum() else ""
    return re.compile(inicio + cuerpo + fin, re.IGNORECASE)


def anonimizar_texto(texto, entidades, mapping):
    for tipo, original, codigo in entidades:
        patron = patron_entidad(original)
        if patron.search(texto):
            mapping.setdefault(codigo, {"tipo": tipo, "valores": []})
            if original not in mapping[codigo]["valores"]:
                mapping[codigo]["valores"].append(original)
            texto = patron.sub(codigo, texto)

    for tipo, patron in PATRONES:
        def reemplazo(m):
            valor = m.group(1) if m.groups() else m.group(0)
            for cod, info in mapping.items():
                if info["tipo"] == tipo and valor in info["valores"]:
                    return m.group(0).replace(valor, cod)
            n = sum(1 for c in mapping.values() if c["tipo"] == tipo) + 1
            codigo = f"{tipo}_{n:03d}"
            mapping[codigo] = {"tipo": tipo, "valores": [valor]}
            return m.group(0).replace(valor, codigo)
        texto = patron.sub(reemplazo, texto)

    return texto


def verificar_residuales(texto_total, entidades):
    """
    Doble verificación final:
      a) patrones universales sobrevivientes
      b) NÚCLEOS de las entidades listadas sobrevivientes en cualquier variante
    """
    alertas = []

    # a) Patrones
    for tipo, patron in PATRONES:
        for m in patron.finditer(texto_total):
            alertas.append(f"Posible {tipo} sin anonimizar: '{m.group(0)[:40]}'")

    # b) Núcleos de entidades (p. ej. 'Trade' de 'Trade S.A.S.')
    nucleos_vistos = set()
    for _, original, codigo in entidades:
        for palabra in nucleo_de(original):
            clave = palabra.lower().strip(".")
            if clave in nucleos_vistos:
                continue
            nucleos_vistos.add(clave)
            patron_nucleo = re.compile(
                r".{0,50}\b" + re.escape(palabra.strip(".")) + r"\b.{0,50}",
                re.IGNORECASE,
            )
            for m in patron_nucleo.finditer(texto_total):
                fragmento = " ".join(m.group(0).split())
                alertas.append(
                    f"Variante sobreviviente de '{original}' ({codigo}): ...{fragmento}..."
                )
    return alertas


ENCABEZADOS_ID = re.compile(
    r"(?i)^\s*(nit|n\.i\.t\.?|identificaci[oó]n|c[eé]dula|documento|no\.?\s*id)\s*$"
)


def anonimizar_columnas_id(tabla, mapping):
    """
    Anonimización por contexto de columna (tablas tipo exógena): si un
    encabezado es NIT/identificación/cédula/documento, los valores numéricos
    de esa columna se codifican aunque no tengan formato con separadores.
    """
    datos = tabla["datos"]
    for idx_enc, fila in enumerate(datos):
        cols_id = [j for j, c in enumerate(fila) if c and ENCABEZADOS_ID.match(str(c))]
        if not cols_id:
            continue
        # Filas siguientes: codificar valores numéricos de 6-11 dígitos
        for fila_datos in datos[idx_enc + 1:]:
            for j in cols_id:
                if j >= len(fila_datos):
                    continue
                valor = str(fila_datos[j]).strip()
                if re.fullmatch(r"\d{6,11}", valor):
                    codigo = None
                    for cod, info in mapping.items():
                        if info["tipo"] == "NIT" and valor in info["valores"]:
                            codigo = cod
                            break
                    if codigo is None:
                        n = sum(1 for c in mapping.values() if c["tipo"] == "NIT") + 1
                        codigo = f"NIT_{n:03d}"
                        mapping[codigo] = {"tipo": "NIT", "valores": [valor]}
                    fila_datos[j] = codigo
        break  # solo la primera fila de encabezados detectada
    return tabla


def anonimizar_json(ruta_json, ruta_entidades):
    with open(ruta_json, encoding="utf-8") as f:
        d = json.load(f)
    entidades = cargar_entidades(ruta_entidades)
    mapping = {}

    d["archivo_origen"] = "DOCUMENTO_ANONIMIZADO.pdf"

    for p in d.get("paginas", []):
        p["texto"] = anonimizar_texto(p["texto"], entidades, mapping)
    for t in d.get("tablas", []):
        t["datos"] = [
            [anonimizar_texto(c, entidades, mapping) if c else c for c in fila]
            for fila in t["datos"]
        ]
        anonimizar_columnas_id(t, mapping)
    d["lineas_membrete_removidas"] = [
        anonimizar_texto(l, entidades, mapping)
        for l in d.get("lineas_membrete_removidas", [])
    ]

    # ---------- Pase de propagación: todo valor ya codificado se codifica
    # en cualquier otra parte del documento donde aparezca ----------
    pares = []
    for codigo, info in mapping.items():
        for valor in info["valores"]:
            pares.append((valor, codigo))
    pares.sort(key=lambda x: len(x[0]), reverse=True)
    for p in d.get("paginas", []):
        for valor, codigo in pares:
            if valor.isdigit():
                p["texto"] = re.sub(
                    r"(?<!\d)" + re.escape(valor) + r"(?!\d)", codigo, p["texto"]
                )
            else:
                p["texto"] = patron_entidad(valor).sub(codigo, p["texto"])

    # ---------- Verificación final sobre TODO el contenido ----------
    texto_total = "\n".join(p["texto"] for p in d.get("paginas", []))
    texto_total += "\n" + "\n".join(
        str(c) for t in d.get("tablas", []) for f in t["datos"] for c in f if c
    )
    texto_total += "\n" + "\n".join(d.get("lineas_membrete_removidas", []))
    residuales = verificar_residuales(texto_total, entidades)

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
        print(f"\n⚠ {len(residuales)} POSIBLES RESIDUALES - RESOLVER ANTES DE ENVIAR:")
        for a in residuales[:30]:
            print(f"   {a}")
        print("\n   -> Agrega las variantes al entidades.txt (con '=') y vuelve a correr.")
    else:
        print("\n✓ Verificación residual: limpia. Apto para enviar.")
    print(f"\nAnónimo:              {ruta_anon}")
    print(f"Mapping (SOLO LOCAL): {ruta_map}")


# ============================================================
# Modo --sugerir
# ============================================================

def sugerir(ruta_json):
    """
    Escanea la extracción y propone candidatos a entidades.
    NO decide nada: imprime un borrador de entidades.txt para revisión humana.
    """
    with open(ruta_json, encoding="utf-8") as f:
        d = json.load(f)

    texto_total = "\n".join(p["texto"] for p in d.get("paginas", []))
    texto_total += "\n" + "\n".join(
        str(c) for t in d.get("tablas", []) for f in t["datos"] for c in f if c
    )
    # Los membretes suelen contener la razón social del contribuyente
    membretes = d.get("lineas_membrete_removidas", [])

    candidatos = Counter()

    # 1) Nombres con sufijo societario (X S.A.S., Y LTDA, ...)
    for m in re.finditer(
        r"([A-ZÁÉÍÓÚÑ][\wÁÉÍÓÚÑáéíóúñ&.\-]*(?:\s+[A-ZÁÉÍÓÚÑ&][\wÁÉÍÓÚÑáéíóúñ&.\-]*){0,5}\s+"
        + SUFIJOS_SOCIETARIOS.pattern + r")",
        texto_total,
    ):
        nombre = " ".join(m.group(1).split())
        candidatos[nombre] += 1

    # 2) Contextos de identidad (razón social, grupo, matriz, representante...)
    contexto_hits = []
    for patron in CONTEXTOS_IDENTIDAD:
        for m in re.finditer(patron, texto_total):
            valor = " ".join(m.group(1).split()).rstrip(",.;")
            contexto_hits.append(valor)
            candidatos[valor] += 3  # peso extra: contexto fuerte

    # 3) Membretes con texto no genérico
    for l in membretes:
        limpio = " ".join(l.replace("#", "").split())
        if len(nucleo_de(limpio)) >= 1 and not limpio.lower().startswith(
            ("estudio", "informe", "documentaci", "página")
        ):
            candidatos[limpio] += 5  # peso máximo: el membrete casi siempre es el contribuyente

    # ---------- Presentación ----------
    print("# ===== CANDIDATOS SUGERIDOS (revisar y editar) =====")
    print("# Copia las líneas que apliquen a casos/casoX/entidades.txt")
    print("# Ajusta el TIPO: EMPRESA (contribuyente), EMPRESA_REL (relacionada),")
    print("# GRUPO (matriz/grupo), PERSONA. Las comparables públicas NO se listan.")
    print("#")
    frecuentes = [(n, c) for n, c in candidatos.most_common(25) if c >= 2]
    if not frecuentes:
        frecuentes = candidatos.most_common(15)
    for nombre, peso in frecuentes:
        print(f"EMPRESA|{nombre}    # peso {peso}")
    if contexto_hits:
        print("#")
        print("# Detectados por contexto de identidad (alta prioridad):")
        for v in dict.fromkeys(contexto_hits):
            print(f"#   -> {v}")


# ============================================================
# Re-identificación
# ============================================================

def reidentificar(ruta_borrador, ruta_mapping):
    with open(ruta_mapping, encoding="utf-8") as f:
        mapping = json.load(f)
    with open(ruta_borrador, encoding="utf-8") as f:
        texto = f.read()
    for codigo, info in mapping.items():
        texto = texto.replace(codigo, info["valores"][0])
    salida = Path(ruta_borrador).with_name(
        Path(ruta_borrador).stem + "_REIDENTIFICADO" + Path(ruta_borrador).suffix
    )
    with open(salida, "w", encoding="utf-8") as f:
        f.write(texto)
    print(f"Borrador re-identificado: {salida}")
    print("Recuerda: contiene datos reales. Manejo local únicamente.")


if __name__ == "__main__":
    if sys.argv[1] == "--sugerir":
        sugerir(sys.argv[2])
    elif sys.argv[1] == "--reidentificar":
        reidentificar(sys.argv[2], sys.argv[3])
    else:
        anonimizar_json(sys.argv[1], sys.argv[2])
