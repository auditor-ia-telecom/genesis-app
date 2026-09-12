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
RE_CATALOGO = re.compile(r"^\s*(\d{3})\s+(\d{6,})\s+(.+)$")  # Pos / Documento / Catálogo / Descripción...

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
    catalogo: str
    descripcion: str
    cantidad: float
    udm: str
    elemento_pep: str | None = None


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
                texto_ocr = pytesseract.image_to_string(imagen, lang="spa")
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
    Se hace un parseo tolerante línea por línea; ajustar el regex si Telecom
    cambia el layout del remito.
    """
    items = []
    pep_actual = None
    for linea in texto.splitlines():
        linea = linea.strip()
        if not linea:
            continue

        pep_match = RE_ELEMENTO_PEP.search(linea)
        if pep_match:
            pep_actual = pep_match.group(1)

        m = RE_CATALOGO.match(linea)
        if m:
            _pos, catalogo, resto = m.groups()
            # Últimos dos tokens suelen ser Cantidad y U.M.
            partes = resto.rsplit(maxsplit=2)
            if len(partes) == 3:
                descripcion, cantidad_str, udm = partes
                try:
                    cantidad = float(cantidad_str.replace(".", "").replace(",", "."))
                except ValueError:
                    continue
                items.append(
                    ItemRemito(
                        catalogo=catalogo,
                        descripcion=descripcion.strip(),
                        cantidad=cantidad,
                        udm=udm.strip(),
                        elemento_pep=pep_actual,
                    )
                )
    return items


def procesar_remito(file_bytes: bytes) -> RemitoExtraido:
    texto = _extraer_texto_pdf(file_bytes)
    tipo_almacen = detectar_tipo_almacen(texto)
    items = parsear_items(texto)
    return RemitoExtraido(tipo_almacen=tipo_almacen, items=items, texto_crudo=texto)
