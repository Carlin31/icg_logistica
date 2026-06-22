"""
logic/groq_logic.py
Integración con Groq API para generación automática de nombres de rutas.

Nombre generado: "<docs_agrupados>_<zona_LLM>"
Ejemplo:         "BB2822/35/63_ROYAN_3 VALLES"

  - La parte de documentos se calcula localmente agrupando documentos
    de mayoristas consecutivos (no interrumpidos por paradas de sucursales).
  - La parte de zona la genera el LLM a partir de los nombres de sucursales.

Si GROQ_API_KEY no está configurada, se usa un fallback local sin LLM.
"""
import os
import json
import urllib.request
import urllib.error

GROQ_API_URL  = "https://api.groq.com/openai/v1/chat/completions"
GROQ_MODEL    = "llama-3.1-8b-instant"
GROQ_TIMEOUT  = 8   # segundos


def _api_key() -> str:
    return os.getenv("GROQ_API_KEY", "")


# ── Agrupación de documentos ──────────────────────────────────────────────────

def agrupar_documentos(paradas: list) -> str:
    """
    Construye la parte de documentos del nombre de ruta.

    Recorre las paradas ordenadas por `orden`. Cada secuencia consecutiva de
    mayoristas (sin sucursales intermedias) forma un grupo. Los documentos
    dentro del grupo se unen con '/' y los grupos entre sí con '_'.

    Ejemplo de secuencia:
        suc → may(BB2822) → may(35) → suc → may(63)
    Resultado: "BB2822/35_63"

    Parámetros
    ----------
    paradas : list de dicts con campos:
        tipo       : 'sucursal' | 'mayorista'
        documento  : str  (identificador del pedido)
        orden      : int  (posición en la secuencia)
    """
    paradas_ord = sorted(paradas, key=lambda p: p.get("orden") or 9999)

    grupos: list[list[str]] = []
    grupo_actual: list[str] = []

    for p in paradas_ord:
        if p.get("tipo") == "mayorista":
            doc = str(p.get("documento") or p.get("id_cliente") or "").strip()
            if doc:
                grupo_actual.append(doc)
        else:
            # Parada de sucursal → cierra el grupo actual
            if grupo_actual:
                grupos.append(grupo_actual)
                grupo_actual = []

    if grupo_actual:
        grupos.append(grupo_actual)

    return "_".join("/".join(g) for g in grupos)


# ── Nombre de zona con LLM ────────────────────────────────────────────────────

def generar_nombre_zona(nombres_sucursales: list) -> str:
    """
    Llama a Groq para generar un nombre corto y representativo de la zona.

    Si la API no está disponible o falla, devuelve un fallback basado en
    las primeras palabras de los nombres de sucursal.

    Parámetros
    ----------
    nombres_sucursales : list[str]  — nombres de las sucursales en la ruta.

    Retorna
    -------
    str — nombre corto, p. ej. "ROYAN_3 VALLES"
    """
    nombres = [str(n).upper().strip() for n in nombres_sucursales if str(n).strip()]
    if not nombres:
        return "RUTA"

    api_key = _api_key()
    if not api_key:
        return _fallback_zona(nombres)

    prompt = (
        "Eres un asistente de logística. Genera un nombre corto (máximo 4 palabras) "
        "que identifique rápidamente la zona de las siguientes sucursales de entrega:\n"
        f"{', '.join(nombres)}\n\n"
        "Reglas:\n"
        "- Usa el nombre más representativo o la abreviatura geográfica.\n"
        "- Separa conceptos distintos con guión bajo (_).\n"
        "- SOLO devuelve el nombre, sin explicaciones ni puntuación final.\n"
        "Ejemplo de salida: ROYAN_3 VALLES"
    )

    try:
        payload = json.dumps({
            "model": GROQ_MODEL,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 25,
            "temperature": 0.2,
        }).encode("utf-8")

        req = urllib.request.Request(
            GROQ_API_URL,
            data=payload,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
        )
        with urllib.request.urlopen(req, timeout=GROQ_TIMEOUT) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            nombre = data["choices"][0]["message"]["content"].strip()
            # Limpiar líneas extra y truncar
            nombre = nombre.splitlines()[0].strip().rstrip(".")
            return nombre[:60]

    except urllib.error.HTTPError as e:
        print(f"[groq_logic] HTTP {e.code}: {e.reason}")
    except Exception as e:
        print(f"[groq_logic] Error al llamar Groq: {e}")

    return _fallback_zona(nombres)


def _fallback_zona(nombres: list) -> str:
    """Fallback sin LLM: usa las primeras palabras de los primeros 3 nombres."""
    partes = []
    for n in nombres[:3]:
        primera = n.split()[0] if n.split() else n
        if primera and primera not in partes:
            partes.append(primera)
    return "_".join(partes) if partes else "RUTA"


# ── Nombre completo de ruta ───────────────────────────────────────────────────

def generar_nombre_ruta(paradas: list, nombres_sucursales: list) -> str:
    """
    Construye el nombre completo de la ruta combinando documentos y zona LLM.

    Formato: "<documentos_agrupados>_<zona>"
    Ejemplo: "BB2822/35/63_ROYAN_3 VALLES"

    Si la ruta no tiene mayoristas, devuelve solo la zona.
    Si no tiene sucursales, devuelve solo los documentos.

    Parámetros
    ----------
    paradas            : list de paradas con tipo, documento y orden.
    nombres_sucursales : list[str] — nombres de sucursales para el LLM.
    """
    docs_str = agrupar_documentos(paradas)
    zona_str = generar_nombre_zona(nombres_sucursales)

    if docs_str and zona_str and zona_str != "RUTA":
        return f"{docs_str}_{zona_str}"
    if docs_str:
        return docs_str
    return zona_str or "RUTA"


# ── Nombre de población para PDF ──────────────────────────────────────────────

def generar_nombre_poblacion(nombres_mayoristas: list) -> str:
    """
    Infers a short city/population name from mayorista names using Groq.
    Used in PDF generation when the 'poblacion' field is not in the database.
    Returns empty string if inference is not possible or Groq is unavailable.
    """
    nombres = [str(n).upper().strip() for n in nombres_mayoristas if str(n).strip()]
    if not nombres:
        return ""

    api_key = _api_key()
    if not api_key:
        first = nombres[0].split()
        return first[0] if first else ""

    prompt = (
        "Eres un asistente de logística. Infiere el nombre de la ciudad o población "
        "de entrega (máximo 3 palabras, en MAYÚSCULAS) a partir de estos nombres de clientes:\n"
        f"{', '.join(nombres[:5])}\n\n"
        "Devuelve SOLO el nombre de la ciudad, sin explicaciones. "
        "Si no puedes inferirlo, responde ZONA."
    )

    try:
        payload = json.dumps({
            "model": GROQ_MODEL,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 12,
            "temperature": 0.1,
        }).encode("utf-8")

        req = urllib.request.Request(
            GROQ_API_URL, data=payload,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
        )
        with urllib.request.urlopen(req, timeout=GROQ_TIMEOUT) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            nombre = data["choices"][0]["message"]["content"].strip()
            nombre = nombre.splitlines()[0].strip().rstrip(".").upper()
            return nombre[:40] if nombre and nombre != "ZONA" else ""

    except Exception as e:
        print(f"[groq_logic] generar_nombre_poblacion: {e}")

    return ""
