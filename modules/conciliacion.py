"""
modules/conciliacion.py — Módulo 5: Conciliación Mensual de Instalaciones (Triple Match).

Cruza el CSV mensual crudo de Telecom (columnas NRO OT, CODIGO SAP S4, IMPORTE, ...)
contra los cierres propios de técnicos, pintando en verde las coincidencias
y en rojo los desvíos.
"""
import streamlit as st
import pandas as pd

from config import get_client, get_empresa_activa, get_id_empresa_activa



def render():
    client = get_client()
    empresa = get_empresa_activa()
    id_empresa = get_id_empresa_activa()

    st.header(f"5️⃣ Conciliación Mensual de Instalaciones — {empresa}")

    periodo = st.text_input("Período (AAAA-MM)", value=pd.Timestamp.today().strftime("%Y-%m"))

    col1, col2 = st.columns(2)
    with col1:
        st.write("**CSV de Telecom (crudo)**")
        csv_telecom = st.file_uploader("NRO OT, CODIGO SAP S4, IMPORTE...", type=["csv"], key="csv_telecom")
    with col2:
        st.write("**Cierres propios (técnicos)**")
        csv_propio = st.file_uploader("NRO OT, IMPORTE cierre técnico", type=["csv"], key="csv_propio")

    if not (csv_telecom and csv_propio):
        _mostrar_historial(client, id_empresa, periodo)
        return

    df_telecom = pd.read_csv(csv_telecom)
    df_propio = pd.read_csv(csv_propio)

    # Normalización tolerante de nombres de columnas
    df_telecom.columns = [c.strip().upper() for c in df_telecom.columns]
    df_propio.columns = [c.strip().upper() for c in df_propio.columns]

    col_ot_telecom = next((c for c in df_telecom.columns if "OT" in c), None)
    col_importe_telecom = next((c for c in df_telecom.columns if "IMPORTE" in c), None)
    col_sap = next((c for c in df_telecom.columns if "SAP" in c), None)
    col_ot_propio = next((c for c in df_propio.columns if "OT" in c), None)
    col_importe_propio = next((c for c in df_propio.columns if "IMPORTE" in c), None)

    if not all([col_ot_telecom, col_importe_telecom, col_ot_propio, col_importe_propio]):
        st.error("No se pudieron identificar las columnas NRO OT / IMPORTE en alguno de los CSV. Revisá los encabezados.")
        return

    merged = df_telecom.merge(
        df_propio, left_on=col_ot_telecom, right_on=col_ot_propio, how="outer", suffixes=("_telecom", "_propio")
    )
    merged["estado_match"] = merged.apply(
        lambda r: "coincide"
        if pd.notna(r[f"{col_importe_telecom}_telecom"]) and pd.notna(r[f"{col_importe_propio}_propio"])
        and abs(r[f"{col_importe_telecom}_telecom"] - r[f"{col_importe_propio}_propio"]) < 0.01
        else "desvio",
        axis=1,
    )

    def _color(row):
        color = "background-color: #d4edda" if row["estado_match"] == "coincide" else "background-color: #f8d7da"
        return [color] * len(row)

    st.write(f"Resultado del cruce — período {periodo}:")
    st.dataframe(merged.style.apply(_color, axis=1), use_container_width=True)

    n_desvios = (merged["estado_match"] == "desvio").sum()
    st.metric("OTs en desvío (reclamo inmediato)", n_desvios)

    if st.button("💾 Guardar resultado de conciliación", type="primary"):
        registros = []
        for _, row in merged.iterrows():
            nro_ot = row.get(col_ot_telecom) or row.get(col_ot_propio)
            if pd.isna(nro_ot):
                continue
            registros.append(
                {
                    "id_empresa": id_empresa,
                    "periodo": periodo,
                    "nro_ot": str(nro_ot),
                    "codigo_sap_s4": str(row.get(col_sap, "")) if col_sap else None,
                    "importe_telecom": row.get(f"{col_importe_telecom}_telecom"),
                    "importe_cierre_tecnico": row.get(f"{col_importe_propio}_propio"),
                    "estado_match": row["estado_match"],
                }
            )
        if registros:
            client.table("conciliacion_instalaciones").upsert(
                registros, on_conflict="id_empresa,periodo,nro_ot"
            ).execute()
            st.success(f"{len(registros)} registros de conciliación guardados para {periodo}.")

    _mostrar_historial(client, id_empresa, periodo)


def _mostrar_historial(client, id_empresa: int, periodo: str):
    st.divider()
    st.subheader(f"Histórico guardado — {periodo}")
    datos = (
        client.table("conciliacion_instalaciones")
        .select("*")
        .eq("id_empresa", id_empresa)
        .eq("periodo", periodo)
        .execute()
        .data
    )
    if datos:
        st.dataframe(pd.DataFrame(datos), use_container_width=True)
    else:
        st.info("Todavía no hay conciliaciones guardadas para este período.")
