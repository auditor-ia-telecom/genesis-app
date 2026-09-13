"""
utils/ocr_parser.py

Los remitos de Telecom (Vale de Entrega) llegan como PDF escaneado
(sin capa de texto), como se confirmó al inspeccionar los archivos de
Carpeta_2. Por eso el flujo es: PDF -> imagen por página -> OCR -> regex.

Este módulo no asume tesseract preinstalado en Streamlit Cloud: se declara
como dependencia de sistema en packages.txt (ver README) y se referencia acá.
"""
import re
import io
from dataclasses import dataclass, field

import pdfplumber
import pytesseract
from PIL import Image

# Prefijos de "Elemento PEP" observados en los remitos reales (Carpeta_2)
PREFIJO_A_TIPO_ALMACEN = {
    "M513": "Instalaciones",
    "C250": "Obras",
    "M250": "Mantenimiento",
    "P250": "Postes",
}

RE_ELEMENTO_PEP = re.compile(r"\b([MCP]\d{3})\b")
# Estructura real observada en los remitos: Pos | Documento | Catálogo | Descripción... | Cantidad | U.M.
# 'Documento' es el número del propio remito (cambia por remito, y hasta el
# OCR lo lee distinto entre renglones del mismo documento) — NO sirve como
# identificador de material. 'Catálogo' sí es el código SAP estable.
RE_POS_DOC_CATALOGO = re.compile(r"^\s*(\d{3})\s+(\d{6,})\s+(\d{6,})\s+(.+)$")

# Cantidad (+ unidad opcional) al FINAL del renglón, buscada como patrón
# independiente en vez de asumir siempre "las últimas dos palabras son
# cantidad+UM": en escaneos reales, Tesseract a veces pierde la unidad
# (ej. remito real donde leyó "...SERIAL 4" sin el "UN" final). Si se asume
# rígidamente cantidad+UM y falta la UM, el intento de convertir la palabra
# anterior a número falla y el ítem se pierde en silencio — exactamente el
# bug reportado. Con este patrón, la unidad es opcional.
RE_CANTIDAD_UDM_FINAL = re.compile(
    r"(?P<cantidad>\d{1,3}(?:[.,]\d{3})*(?:[.,]\d+)?)\s*(?P<udm>[A-Za-zÁÉÍÓÚÑáéíóúñ]{1,5})?\s*$"
)

# Patrones compartidos con modules/tabla_maestra.py para reconocer:
#   - NPA de Telecom (Nota de Pedido Abierta): prefijo 7600...
#   - Pedido de Compra vía SAP Business Network / Ariba: prefijo 7800...
# Se usa {6,10} en vez de {6} fijo porque el largo total observado en los PDF
# reales (7600040877, 7800420385) es de 10 dígitos, pero Telecom no garantiza
# que ese largo sea estable a futuro (por eso NO se fija en 7 dígitos exactos).
RE_NPA = re.compile(r"\b(7600\d{6,10})\b")
RE_PEDIDO_ARIBA = re.compile(r"\b(7800\d{6,10})\b")


@dataclass
class ItemRemito:
    numero_documento: str
    catalogo: str
    descripcion: str
    cantidad: float
    udm: str
    elemento_pep: str | None = None
    necesita_revision: bool = False  # True si el OCR no permitió separar cantidad con confianza


@dataclass
class RemitoExtraido:
    tipo_almacen: str | None
    items: list[ItemRemito] = field(default_factory=list)
    texto_crudo: str = ""


def _extraer_texto_pdf(file_bytes: bytes) -> str:
    """Intenta capa de texto nativa; si no hay (escaneado), cae a OCR por página."""
    texto_total = []
    with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
        for page in pdf.pages:
            texto = page.extract_text()
            if texto and texto.strip():
                texto_total.append(texto)
            else:
                imagen = page.to_image(resolution=200).original
                # PSM 6 ("bloque uniforme de texto") en vez del modo automático
                # (PSM 3, default): confirmado con casos reales que cuando la
                # columna 'Elemento PEP' viene vacía, deja un hueco horizontal
                # grande antes de Cantidad/U.M., y el modo automático de
                # Tesseract descarta esa franja aislada de números como si no
                # fuera texto. PSM 6 la recupera sin perder el resto del
                # documento (verificado contra remitos reales de C250 y M513).
                texto_ocr = pytesseract.image_to_string(imagen, lang="spa", config="--psm 6")
                texto_total.append(texto_ocr)
    return "\n".join(texto_total)


def detectar_tipo_almacen(texto: str) -> str | None:
    """Busca M513 / C250 / M250 / P250 en el texto y devuelve el tipo de almacén."""
    match = RE_ELEMENTO_PEP.search(texto)
    if not match:
        return None
    prefijo = match.group(1)
    return PREFIJO_A_TIPO_ALMACEN.get(prefijo)


def parsear_items(texto: str) -> list[ItemRemito]:
    """Extrae renglones de ítems del Vale de Entrega. El formato real observado es:
    'Pos  Documento  Catálogo  Descripción ... Elemento PEP  Cantidad  U.M.'

    Dos garantías deliberadas, por bugs reales encontrados con documentos
    de producción:
      1. Se usa el campo 'Catálogo' (código SAP del material) para
         identificar el material, NUNCA 'Documento' (número del propio
         remito, que cambia por remito y que el OCR llega a leer distinto
         entre renglones del mismo documento).
      2. Un renglón que matchea Pos+Documento+Catálogo NUNCA se descarta en
         silencio aunque el OCR no haya podido separar cantidad/UM con
         confianza (por ejemplo, cuando el escaneo perdió la unidad al
         final). Se agrega igual con necesita_revision=True para que la
         persona lo vea en la grilla editable de logistica.py y lo corrija
         a mano contra el PDF original, en vez de que el ítem desaparezca
         sin ningún aviso.
      3. Dentro de un mismo remito, todos los renglones (003, 005, 007...)
         DEBEN compartir el mismo N° de Documento que el primero (001) —
         es el mismo número impreso, repetido. Si el OCR lee un Documento
         distinto en un renglón intermedio (típicamente un dígito de más
         mal leído), se lo reemplaza por el del renglón 001 de ese bloque
         y se marca igual necesita_revision=True, en vez de dejar un
         documento "fantasma" que nunca va a coincidir con nada en
         remitos_ingresados.
    """
    items = []
    pep_actual = None
    documento_bloque = None
    for linea in texto.splitlines():
        linea = linea.strip()
        if not linea:
            continue

        pep_match = RE_ELEMENTO_PEP.search(linea)
        if pep_match:
            pep_actual = pep_match.group(1)

        m = RE_POS_DOC_CATALOGO.match(linea)
        if not m:
            continue
        pos, numero_documento, catalogo, resto = m.groups()

        documento_inconsistente = False
        if pos == "001":
            documento_bloque = numero_documento
        elif documento_bloque is not None and numero_documento != documento_bloque:
            documento_inconsistente = True
            numero_documento = documento_bloque  # mejor estimación: el del inicio del bloque

        m_cant = RE_CANTIDAD_UDM_FINAL.search(resto)
        if m_cant:
            cantidad_str = m_cant.group("cantidad")
            udm = (m_cant.group("udm") or "").strip()
            descripcion = resto[: m_cant.start()].strip()
            try:
                cantidad = float(cantidad_str.replace(".", "").replace(",", "."))
                necesita_revision = False
            except ValueError:
                cantidad, necesita_revision = 0.0, True
        else:
            # No se encontró ningún número al final del renglón: se agrega
            # igual con cantidad en blanco en vez de perderse el ítem.
            descripcion = resto.strip()
            cantidad, udm, necesita_revision = 0.0, "", True

        items.append(
            ItemRemito(
                numero_documento=numero_documento,
                catalogo=catalogo,
                descripcion=descripcion or "(revisar contra el PDF original)",
                cantidad=cantidad,
                udm=udm,
                elemento_pep=pep_actual,
                necesita_revision=necesita_revision or documento_inconsistente,
            )
        )
    return items


def procesar_remito(file_bytes: bytes) -> RemitoExtraido:
    texto = _extraer_texto_pdf(file_bytes)
    tipo_almacen = detectar_tipo_almacen(texto)
    items = parsear_items(texto)
    return RemitoExtraido(tipo_almacen=tipo_almacen, items=items, texto_crudo=texto)
