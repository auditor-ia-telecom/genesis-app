"""
modules/home.py — Pantalla de inicio estilo SAP ARIBA.

KPIs rápidos + 4 widgets gráficos, todos filtrados por la empresa activa
(id_empresa resuelto vía config.get_empresa_activa()).
"""
import streamlit as st
import pandas as pd
import plotly.graph_objects as go
import plotly.express as px

from config import get_client, get_empresa_activa, get_id_empresa_activa



def _fetch_kpis(client, id_empresa: int) -> dict:
    res = client.table("vw_kpi_home").select("*").eq("id_empresa", id_empresa).single().execute()
    return res.data or {}


def render():
    client = get_client()
    empresa = get_empresa_activa()
    id_empresa = get_id_empresa_activa()

    st.header(f"🏠 Home — {empresa}")

    # ---------------- Fila de KPIs rápidos ----------------
    kpis = _fetch_kpis(client, id_empresa)
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Pedidos Activos", kpis.get("pedidos_activos", 0))
    c2.metric("Desvíos en Alerta (OTs Rojas)", kpis.get("desvios_alerta", 0))
    c3.metric("Retenido en Exactian", f"$ {kpis.get('retenido_exactian', 0):,.0f}")
    c4.metric("Total Pre-Facturado", f"$ {kpis.get('total_prefacturado', 0):,.0f}")
    c5.metric("Total en ARIBA", f"$ {kpis.get('total_ariba', 0):,.0f}")

    st.divider()

    col_izq, col_der = st.columns(2)

    # ---------------- Widget: Pedidos de Compra (línea, 3 meses) ----------------
    with col_izq:
        st.subheader("Pedidos de Compra (últimos 3 meses)")
        pedidos = (
            client.table("tabla_maestra_pedidos")
            .select("importe, created_at")
            .eq("id_empresa", id_empresa)
            .execute()
            .data
        )
        if pedidos:
            df = pd.DataFrame(pedidos)
            df["created_at"] = pd.to_datetime(df["created_at"])
            df["mes"] = df["created_at"].dt.to_period("M").astype(str)
            serie = df.groupby("mes")["importe"].sum().tail(3).reset_index()
            fig = go.Figure(go.Scatter(x=serie["mes"], y=serie["importe"], mode="lines+markers"))
            fig.update_layout(yaxis_title="ARS", xaxis_title="Mes", height=320)
            st.plotly_chart(fig, use_container_width=True)
        else:
            st.info("Sin pedidos cargados todavía en la Tabla Maestra.")

    # ---------------- Widget: Antigüedad de Vencimiento de Factura ----------------
    with col_der:
        st.subheader("Antigüedad de Vencimiento de Factura")
        facturas = (
            client.table("vw_antiguedad_facturas")
            .select("bucket_vencimiento, importe")
            .eq("id_empresa", id_empresa)
            .execute()
            .data
        )
        if facturas:
            df = pd.DataFrame(facturas)
            resumen = df.groupby("bucket_vencimiento")["importe"].sum().reindex(
                ["0-30", "31-60", "61-90", "90+"]
            ).fillna(0)
            fig = go.Figure(go.Bar(x=resumen.index, y=resumen.values))
            fig.update_layout(yaxis_title="ARS", xaxis_title="Días", height=320)
            st.plotly_chart(fig, use_container_width=True)
        else:
            st.info("Sin facturas AFIP cargadas todavía.")

    st.divider()
    col_izq2, col_der2 = st.columns(2)

    # ---------------- Widget: Eficiencia CCT ----------------
    with col_izq2:
        st.subheader("Eficiencia CCT (por técnico)")
        conciliacion = (
            client.table("conciliacion_instalaciones")
            .select("id_personal, estado_match")
            .eq("id_empresa", id_empresa)
            .execute()
            .data
        )
        if conciliacion:
            df = pd.DataFrame(conciliacion)
            resumen = df["estado_match"].value_counts().reset_index()
            resumen.columns = ["estado", "cantidad"]
            fig = px.pie(resumen, names="estado", values="cantidad", hole=0.4)
            fig.update_layout(height=320)
            st.plotly_chart(fig, use_container_width=True)
        else:
            st.info("Cargá el CSV mensual de Instalaciones para ver este widget.")

    # ---------------- Widget: Tope de Desarraigo ----------------
    with col_der2:
        st.subheader("Tope de Desarraigo (Real vs 15% MO)")
        viaticos = (
            client.table("despachos_viaticos")
            .select("costo_viatico_total, tope_15pct_mo, fecha")
            .eq("id_empresa", id_empresa)
            .order("fecha", desc=True)
            .limit(10)
            .execute()
            .data
        )
        if viaticos:
            df = pd.DataFrame(viaticos)
            fig = go.Figure()
            fig.add_bar(name="Costo Real", x=df["fecha"], y=df["costo_viatico_total"])
            fig.add_bar(name="Tope 15% MO", x=df["fecha"], y=df["tope_15pct_mo"])
            fig.update_layout(barmode="group", yaxis_title="ARS", height=320)
            st.plotly_chart(fig, use_container_width=True)
        else:
            st.info("Sin despachos de viáticos cargados todavía.")
