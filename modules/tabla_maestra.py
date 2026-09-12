"""
modules/tabla_maestra.py — Módulo 6: AFIP, ARIBA y Tabla Maestra de Asociación.

Flujo real (según Carpeta_1 y Carpeta_3):
  A) Subir la ACAT (Acta Excel) -> extrae Nro de Acta + Montos Certificados
     -> estado 'pre_certificado'.
  B) Cargar datos AFIP (Nro Factura, CAE, Vencimiento) -> 'afip_cargado'.
  C) Arrastrar el reporte ARIBA (HES/WE/Importes) -> vincula contra la
     Tabla Maestra (Pedido 7800... <-> NPA 7600...) -> 'aceptado_pendiente_cobro'.

La vinculación Pedido ARIBA <-> NPA es automática al leer el PDF de la OC de
SAP (Carpeta_3/ejemplo de pedido): el propio pedido trae impreso su
'Número de contrato' (7600...), que es el NPA.
"""
import re
import io

import streamlit as st
import pandas as pd
import pdfplumber

from config import get_client, get_empresa_activa, get_id_empresa_activa
from utils.ocr_parser import RE_NPA, RE_PEDIDO_ARIBA  # fuente única de estos patrones

RE_IMPORTE = re.compile(r"Importe:\s*\$?\s*([\d\.,]+)")
RE_PEP_OC = re.compile(r"PEP/OC\b.*?([A-Z0-9\-\.]+)")



def _parse_pedido_ariba_pdf(file_bytes: bytes) -> dict:
    """Extrae Nro de Pedido ARIBA (7800...), Nro de Contrato/NPA (7600...) e importe
    del PDF 'Pedido de compra' exportado por SAP Business Network / Ariba."""
    texto = ""
    with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
        for page in pdf.pages:
            t = page.extract_text()
            if t:
                texto += t + "\n"

    pedido = RE_PEDIDO_ARIBA.search(texto)
    npa = RE_NPA.search(texto)
    importe_match = RE_IMPORTE.search(texto)
    importe = None
    if importe_match:
        importe = float(importe_match.group(1).replace(".", "").replace(",", "."))

    return {
        "numero_pedido_ariba": pedido.group(1) if pedido else None,
        "numero_npa": npa.group(1) if npa else None,
        "importe": importe,
        "texto_crudo": texto,
    }


def _tab_vinculacion_ariba_npa(client, id_empresa: int):
    st.subheader("Vinculación automática Pedido ARIBA ↔ NPA")
    archivo = st.file_uploader(
        "Arrastrá el PDF del Pedido de Compra de SAP (patrón 7800...)", type=["pdf"], key="pdf_pedido"
    )
    if archivo is None:
        return

    datos = _parse_pedido_ariba_pdf(archivo.read())

    col1, col2, col3 = st.columns(3)
    numero_pedido = col1.text_input("N° Pedido ARIBA", value=datos["numero_pedido_ariba"] or "")
    numero_npa = col2.text_input("N° NPA vinculado", value=datos["numero_npa"] or "")
    importe = col3.number_input("Importe (ARS)", value=float(datos["importe"] or 0.0))

    if not datos["numero_pedido_ariba"] or not datos["numero_npa"]:
        st.warning("No se detectaron ambos números automáticamente — completá/corregí los campos antes de guardar.")

    if st.button("🔗 Guardar en Tabla Maestra", type="primary"):
        client.table("tabla_maestra_pedidos").upsert(
            {
                "id_empresa": id_empresa,
                "numero_pedido_ariba": numero_pedido,
                "numero_npa": numero_npa,
                "importe": importe,
                "origen_archivo": archivo.name,
            },
            on_conflict="id_empresa,numero_pedido_ariba",
        ).execute()
        st.success(f"Pedido {numero_pedido} vinculado a NPA {numero_npa}.")

    with st.expander("Texto crudo extraído (debug)"):
        st.text(datos["texto_crudo"][:3000])


def _tab_acta_certificacion(client, id_empresa: int):
    st.subheader("A) Cargar ACAT (Acta de Certificación)")
    archivo = st.file_uploader("Excel del Acta (hoja CERTIFICACION)", type=["xlsx"], key="acta_xlsx")
    if archivo is None:
        return

    # La hoja CERTIFICACION real trae los pares clave/valor desplazados en
    # columnas fijas: (ID, OBRA-TAREA, PEP/OC, Descripción, SUPERVISIÓN TECO,
    # CONTRATISTA) a la izquierda y (FECHA, CERTIF. N°, INICIO/FIN DE OBRA) a la derecha.
    df = pd.read_excel(archivo, sheet_name="CERTIFICACION", header=None)

    def _buscar_valor(etiqueta: str):
        mask = df.apply(lambda row: row.astype(str).str.contains(etiqueta, case=False, na=False)).any(axis=1)
        filas = df[mask]
        if filas.empty:
            return None
        fila = filas.iloc[0]
        idx_etiqueta = fila[fila.astype(str).str.contains(etiqueta, case=False, na=False)].index[0]
        # el valor suele estar 1 o 2 columnas a la derecha de la etiqueta
        for offset in (1, 2, 3):
            if idx_etiqueta + offset in fila.index:
                val = fila[idx_etiqueta + offset]
                if pd.notna(val):
                    return val
        return None

    nro_acta = _buscar_valor("CERTIF")
    obra_tarea = _buscar_valor("OBRA -TAREA") or _buscar_valor("OBRA-TAREA")
    pep_oc = _buscar_valor("PEP/OC")
    sobrestante = _buscar_valor("SUPERVIS")

    # Total certificado: sumar columna 'Total Certificado' de la tabla de ítems LPU
    fila_header = df.apply(lambda r: r.astype(str).str.contains("Total Certificado", na=False)).any(axis=1)
    monto_total = None
    if fila_header.any():
        header_idx = df[fila_header].index[0]
        col_total = df.iloc[header_idx][df.iloc[header_idx].astype(str) == "Total Certificado"].index[0]
        monto_total = pd.to_numeric(df.iloc[header_idx + 1 :][col_total], errors="coerce").sum()

    st.write("Datos detectados (editables):")
    c1, c2 = st.columns(2)
    nro_acta = c1.text_input("N° de Acta", value=str(nro_acta or ""))
    obra_tarea = c1.text_input("Obra / Tarea", value=str(obra_tarea or ""))
    pep_oc = c2.text_input("PEP / OC", value=str(pep_oc or ""))
    sobrestante = c2.text_input("Sobrestante", value=str(sobrestante or ""))
    monto_certificado = st.number_input("Monto Certificado (ARS)", value=float(monto_total or 0.0))

    if st.button("📌 Pasar a Pre-Certificado", type="primary"):
        client.table("actas_certificacion").upsert(
            {
                "id_empresa": id_empresa,
                "nro_acta": nro_acta,
                "obra_tarea": obra_tarea,
                "pep_oc": pep_oc,
                "sobrestante": sobrestante,
                "monto_certificado": monto_certificado,
                "estado": "pre_certificado",
                "origen_archivo": archivo.name,
            },
            on_conflict="id_empresa,nro_acta",
        ).execute()
        st.success(f"Acta N° {nro_acta} cargada como Pre-Certificado.")


def _tab_afip(client, id_empresa: int):
    st.subheader("B) Cargar datos AFIP")
    actas = client.table("actas_certificacion").select("id_acta, nro_acta").eq(
        "id_empresa", id_empresa
    ).execute().data
    if not actas:
        st.info("Cargá primero un Acta en la pestaña A.")
        return

    acta_map = {a["nro_acta"]: a["id_acta"] for a in actas}
    acta_sel = st.selectbox("Acta asociada", list(acta_map.keys()))

    nro_factura = st.text_input("N° Factura")
    cae = st.text_input("CAE")
    vencimiento = st.date_input("Vencimiento CAE")
    importe = st.number_input("Importe (ARS)", min_value=0.0)

    if st.button("💾 Guardar datos AFIP", type="primary"):
        client.table("facturas_afip").insert(
            {
                "id_acta": acta_map[acta_sel],
                "id_empresa": id_empresa,
                "nro_factura": nro_factura,
                "cae": cae,
                "fecha_vencimiento_cae": str(vencimiento),
                "importe": importe,
            }
        ).execute()
        client.table("actas_certificacion").update({"estado": "afip_cargado"}).eq(
            "id_acta", acta_map[acta_sel]
        ).execute()
        st.success("Datos AFIP guardados. Acta pasa a estado 'afip_cargado'.")


def _tab_reporte_ariba(client, id_empresa: int):
    st.subheader("C) Reporte ARIBA (HES / WE) → cierre del lote")
    pedidos = client.table("tabla_maestra_pedidos").select("id_pedido, numero_pedido_ariba").eq(
        "id_empresa", id_empresa
    ).execute().data
    if not pedidos:
        st.info("Vinculá primero un Pedido ARIBA↔NPA en la primera pestaña.")
        return

    archivo = st.file_uploader(
        "Reporte exportado de ARIBA (Excel/HTML con columnas HES / WE / Importe)",
        type=["xlsx", "xls", "html"],
        key="reporte_ariba",
    )
    if archivo is None:
        return

    try:
        if archivo.name.lower().endswith((".html", ".xls")):
            tablas = pd.read_html(archivo)
            df = tablas[0]
        else:
            df = pd.read_excel(archivo)
    except Exception as e:
        st.error(f"No se pudo parsear el archivo automáticamente ({e}). Revisá el formato exportado.")
        return

    st.write("Vista previa del reporte (ajustá el mapeo de columnas si es necesario):")
    st.dataframe(df.head(20), use_container_width=True)

    col_hes = st.selectbox("Columna HES", df.columns)
    col_we = st.selectbox("Columna WE", df.columns)
    col_importe = st.selectbox("Columna Importe", df.columns)
    pedido_map = {p["numero_pedido_ariba"]: p["id_pedido"] for p in pedidos}
    pedido_sel = st.selectbox("Pedido ARIBA a enlazar", list(pedido_map.keys()))

    if st.button("🔗 Enlazar y cerrar lote", type="primary"):
        for _, row in df.iterrows():
            client.table("ariba_hes_we").insert(
                {
                    "id_pedido": pedido_map[pedido_sel],
                    "nro_hes": str(row[col_hes]),
                    "nro_we": str(row[col_we]),
                    "importe": pd.to_numeric(row[col_importe], errors="coerce"),
                    "origen_archivo": archivo.name,
                }
            ).execute()
        client.table("tabla_maestra_pedidos").update({"estado": "vinculado"}).eq(
            "id_pedido", pedido_map[pedido_sel]
        ).execute()
        st.success(f"{len(df)} registros HES/WE enlazados. Lote pasa a 'Aceptado / Pendiente de Cobro'.")


def render():
    client = get_client()
    empresa = get_empresa_activa()
    id_empresa = get_id_empresa_activa()

    st.header(f"6️⃣ AFIP / ARIBA / Tabla Maestra — {empresa}")
    tabs = st.tabs(
        [
            "🔗 Vinculación ARIBA↔NPA",
            "A) Acta de Certificación",
            "B) Datos AFIP",
            "C) Reporte ARIBA (cierre)",
        ]
    )
    with tabs[0]:
        _tab_vinculacion_ariba_npa(client, id_empresa)
    with tabs[1]:
        _tab_acta_certificacion(client, id_empresa)
    with tabs[2]:
        _tab_afip(client, id_empresa)
    with tabs[3]:
        _tab_reporte_ariba(client, id_empresa)
