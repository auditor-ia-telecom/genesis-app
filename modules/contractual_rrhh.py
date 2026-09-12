"""
modules/contractual_rrhh.py — Módulo 1: Gestión Contractual y RRHH (Exactian).

Incluye:
  - Alta de NPA (Nota de Pedido Abierta) por empresa.
  - LPU indexada: alta de vigencias (80% IVS + 20% IPC) e ítems.
  - Legajos de personal + "Semáforo Exactian": si el ART está vencido o
    rechazado, se bloquea la asignación (visualmente acá; el bloqueo real de
    agenda se aplica también como validación en Despacho/Parte Diario).
"""
import streamlit as st
import pandas as pd

from config import get_client, get_empresa_activa, get_id_empresa_activa



def _tab_npa(client, id_empresa: int):
    st.subheader("Contratos NPA")
    with st.form("form_npa"):
        c1, c2, c3 = st.columns(3)
        numero_npa = c1.text_input("N° NPA (patrón 7600...)")
        proveedor = c1.text_input("Proveedor / Contratista")
        fecha_emision = c2.date_input("Fecha de emisión")
        fecha_inicio = c2.date_input("Fecha inicio")
        fecha_fin = c3.date_input("Fecha fin")
        monto_maximo = c3.number_input("Monto máximo (ARS)", min_value=0.0)
        if st.form_submit_button("Guardar NPA", type="primary"):
            client.table("contratos_npa").upsert(
                {
                    "id_empresa": id_empresa,
                    "numero_npa": numero_npa,
                    "proveedor_nombre": proveedor,
                    "fecha_emision": str(fecha_emision),
                    "fecha_inicio": str(fecha_inicio),
                    "fecha_fin": str(fecha_fin),
                    "monto_maximo": monto_maximo,
                },
                on_conflict="id_empresa,numero_npa",
            ).execute()
            st.success(f"NPA {numero_npa} guardada.")

    npas = client.table("contratos_npa").select("*").eq("id_empresa", id_empresa).execute().data
    if npas:
        st.dataframe(pd.DataFrame(npas), use_container_width=True)


def _tab_lpu(client, id_empresa: int):
    st.subheader("LPU — Lista de Precios Única (indexada 80% IVS + 20% IPC)")
    st.caption("La LPU es compartida por Telecom entre contratistas; acá se administra su vigencia e ítems.")

    with st.expander("➕ Nueva vigencia"):
        c1, c2, c3 = st.columns(3)
        fecha_vigencia = c1.date_input("Fecha de vigencia")
        factor_ivs = c2.number_input("Variación IVS (%)", format="%.5f")
        factor_ipc = c3.number_input("Variación IPC (%)", format="%.5f")
        if st.button("Crear vigencia"):
            client.table("lpu_vigencias").insert(
                {"fecha_vigencia": str(fecha_vigencia), "factor_ivs": factor_ivs, "factor_ipc": factor_ipc}
            ).execute()
            st.success("Vigencia creada. El factor combinado se calcula automáticamente en la base.")

    vigencias = client.table("lpu_vigencias").select("*").order("fecha_vigencia", desc=True).execute().data
    if vigencias:
        st.dataframe(pd.DataFrame(vigencias), use_container_width=True)

    st.divider()
    st.write("Carga masiva de ítems LPU (desde el Excel LPU-U provisto por Telecom)")
    archivo = st.file_uploader("Excel LPU (hoja 'LPU - U' o similar)", type=["xlsx"])
    if archivo and vigencias:
        vigencia_sel = st.selectbox(
            "Vigencia a asociar", [v["fecha_vigencia"] for v in vigencias]
        )
        id_vigencia = next(v["id_vigencia"] for v in vigencias if v["fecha_vigencia"] == vigencia_sel)

        hoja = st.text_input("Nombre de la hoja a leer", value="LPU - U")
        if st.button("Previsualizar"):
            df_raw = pd.read_excel(archivo, sheet_name=hoja, header=None)
            # La fila de encabezados reales está donde aparece 'S4' — se detecta dinámicamente.
            header_row = df_raw.apply(lambda r: (r == "S4").any(), axis=1).idxmax()
            df = pd.read_excel(archivo, sheet_name=hoja, header=header_row)
            st.session_state["_lpu_preview"] = df
            st.dataframe(df.head(20), use_container_width=True)

        if "_lpu_preview" in st.session_state and st.button("✅ Confirmar carga de ítems", type="primary"):
            df = st.session_state["_lpu_preview"]
            registros = []
            for _, row in df.iterrows():
                if pd.isna(row.get("S4")):
                    continue
                registros.append(
                    {
                        "id_vigencia": id_vigencia,
                        "codigo_sap_s4": str(row.get("S4")),
                        "codigo_legacy": str(row.get("CÓDIGO", "")),
                        "descripcion": str(row.get("NUEVA DESCRIPCIÓN", "")),
                        "udm": str(row.get("UdM", "")),
                        "precio_mantenimiento": pd.to_numeric(row.get("MANTENIMIENTO"), errors="coerce"),
                        "precio_obras": pd.to_numeric(row.get("OBRAS"), errors="coerce"),
                    }
                )
            if registros:
                client.table("lpu_items").upsert(registros, on_conflict="id_vigencia,codigo_sap_s4").execute()
                st.success(f"{len(registros)} ítems LPU cargados.")


def _tab_legajos(client, id_empresa: int):
    st.subheader("Legajos de personal + Semáforo Exactian (ART)")

    with st.form("form_personal"):
        c1, c2, c3 = st.columns(3)
        nombre = c1.text_input("Nombre completo (ej. NIEVA JONATHAN)")
        categoria = c2.text_input("Categoría")
        dni = c3.text_input("DNI")
        art_estado = c1.selectbox("Estado ART", ["vigente", "vencido", "rechazado", "pendiente"])
        art_vencimiento = c2.date_input("Vencimiento ART")
        if st.form_submit_button("Guardar legajo", type="primary"):
            client.table("personal").insert(
                {
                    "id_empresa": id_empresa,
                    "nombre_completo": nombre,
                    "categoria": categoria,
                    "dni": dni,
                    "art_estado": art_estado,
                    "art_vencimiento": str(art_vencimiento),
                }
            ).execute()
            st.success(f"Legajo de {nombre} guardado.")

    personal = client.table("personal").select("*").eq("id_empresa", id_empresa).execute().data
    if not personal:
        return

    df = pd.DataFrame(personal)

    def _semaforo(estado):
        return {"vigente": "🟢", "pendiente": "🟡", "vencido": "🔴", "rechazado": "🔴"}.get(estado, "⚪")

    df["Semáforo"] = df["art_estado"].apply(_semaforo)
    st.dataframe(
        df[["nombre_completo", "categoria", "art_estado", "art_vencimiento", "Semáforo"]],
        use_container_width=True,
    )
    bloqueados = df[df["art_estado"].isin(["vencido", "rechazado"])]
    if not bloqueados.empty:
        st.error(
            f"⚠️ {len(bloqueados)} operario(s) con ART vencida/rechazada: NO deben asignarse en agenda "
            f"({', '.join(bloqueados['nombre_completo'])})."
        )


def render():
    client = get_client()
    empresa = get_empresa_activa()
    id_empresa = get_id_empresa_activa()

    st.header(f"1️⃣ Contractual y RRHH (Exactian) — {empresa}")
    tab1, tab2, tab3 = st.tabs(["📄 Contratos NPA", "💲 LPU", "👷 Legajos / Semáforo Exactian"])
    with tab1:
        _tab_npa(client, id_empresa)
    with tab2:
        _tab_lpu(client, id_empresa)
    with tab3:
        _tab_legajos(client, id_empresa)
