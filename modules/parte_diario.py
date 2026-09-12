"""
modules/parte_diario.py — Módulo 4: Procesamiento de Parte Diario de Obra.

Extrae del PDF del Parte Diario (formato real visto en Carpeta_2:
'Parte Diario - CATAMARCA', con tabla de cuadrillas: Motivo/Equipo/Zona/
Descripción/Cantidad y nómina de personal con patentes) los campos:
  - Proyecto (ej. 'ARATO-26086.AND1-Expansion FTTH')
  - Zonas de trabajo (ej. 'NAP AA-14')
  - Patentes de vehículos
  - Operarios en calle
  - % de avance físico

Guarda todo históricamente para alimentar el Dashboard Estadístico.
"""
import re
import io

import streamlit as st
import pandas as pd
import pdfplumber

from config import get_client, get_empresa_activa, get_id_empresa_activa

RE_PROYECTO = re.compile(r"Parte Diario\s+([A-Z0-9\-\.]+(?:Expansion|FTTH)?[\w\-\.]*)", re.IGNORECASE)
RE_NAP = re.compile(r"\bNAP\s+[A-Z]{1,3}-\d{1,3}\b")
RE_PATENTE = re.compile(r"\b[A-Z]{2,3}[\s\-]?\d{3}[\s\-]?[A-Z]{0,2}\b")
RE_PORCENTAJE = re.compile(r"(\d{1,3})\s*%")



def _extraer_texto(file_bytes: bytes) -> str:
    texto = ""
    with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
        for page in pdf.pages:
            t = page.extract_text()
            if t:
                texto += t + "\n"
    return texto


def _parsear_parte(texto: str) -> dict:
    proyectos = RE_PROYECTO.findall(texto)
    zonas = sorted(set(RE_NAP.findall(texto)))
    patentes = sorted(set(RE_PATENTE.findall(texto)))
    porcentajes = [int(p) for p in RE_PORCENTAJE.findall(texto)]
    avance_promedio = sum(porcentajes) / len(porcentajes) if porcentajes else None

    return {
        "proyectos": proyectos,
        "zonas": zonas,
        "patentes": patentes,
        "avance_promedio": avance_promedio,
    }


def render():
    client = get_client()
    empresa = get_empresa_activa()
    id_empresa = get_id_empresa_activa()

    st.header(f"4️⃣ Parte Diario de Obra — {empresa}")

    archivo = st.file_uploader("PDF del Parte Diario de Obras Grandes", type=["pdf"])
    if archivo is None:
        _mostrar_historial(client, id_empresa)
        return

    texto = _extraer_texto(archivo.read())
    if not texto.strip():
        st.error("El PDF no tiene texto extraíble (posible escaneo). Este módulo requiere OCR — ver utils/ocr_parser.py")
        return

    datos = _parsear_parte(texto)

    st.write("Datos detectados automáticamente (editables):")
    fecha = st.date_input("Fecha del parte")
    proyecto = st.text_input("Proyecto", value=datos["proyectos"][0] if datos["proyectos"] else "")
    zona = st.multiselect("Zonas de trabajo (NAP)", options=datos["zonas"], default=datos["zonas"])
    avance = st.number_input(
        "% de avance físico (promedio detectado)", value=float(datos["avance_promedio"] or 0.0)
    )

    st.write("Patentes detectadas:", ", ".join(datos["patentes"]) or "ninguna")

    if st.button("💾 Guardar parte diario", type="primary"):
        parte = client.table("partes_diarios").insert(
            {
                "id_empresa": id_empresa,
                "fecha": str(fecha),
                "proyecto": proyecto,
                "zona": ", ".join(zona),
                "porcentaje_avance": avance,
                "origen_archivo": archivo.name,
            }
        ).execute()
        st.success(f"Parte diario guardado (ID {parte.data[0]['id_parte']}). Alimentará el Dashboard Estadístico.")

    with st.expander("Texto crudo extraído (debug)"):
        st.text(texto[:3000])

    _mostrar_historial(client, id_empresa)


def _mostrar_historial(client, id_empresa: int):
    st.divider()
    st.subheader("Histórico de partes diarios")
    partes = client.table("partes_diarios").select("*").eq(
        "id_empresa", id_empresa
    ).order("fecha", desc=True).execute().data
    if partes:
        st.dataframe(pd.DataFrame(partes), use_container_width=True)
    else:
        st.info("Todavía no hay partes diarios cargados.")
