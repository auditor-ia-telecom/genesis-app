"""
modules/despacho_viaticos.py — Módulo 3: Despacho y Control de Topes de Viáticos.

Cálculo (franquicia de 80km restada antes de tarifar):
    km_excedente        = max(0, km_totales - km_franquicia)
    costo_combustible   = km_excedente * valor_referencia_km
    costo_viatico_total = costo_combustible + desayuno + almuerzo + cena + alojamiento
    tope_15pct_mo       = valor_mo_tarea * 0.15
    bloqueado           = costo_viatico_total > tope_15pct_mo

Gobierno de excepciones (por rol, ver app.py):
    - Si NO está bloqueado, se guarda directo, como siempre.
    - Si SÍ está bloqueado, no se guarda automáticamente: queda pendiente en
      sesión y requiere que un usuario con rol 'dueño' apruebe la excepción
      con un botón dedicado. Un 'coordinador' ve el mismo botón pero
      deshabilitado (no cosmético: el insert no ocurre si no es dueño).
"""
import streamlit as st
import pandas as pd

from config import get_client, get_empresa_activa, get_id_empresa_activa

FRANQUICIA_KM_DEFAULT = 80.0


def _guardar_despacho(client, datos: dict):
    client.table("despachos_viaticos").insert(datos).execute()


def render():
    client = get_client()
    empresa = get_empresa_activa()
    id_empresa = get_id_empresa_activa()
    rol_actual = st.session_state.get("rol_actual")
    usuario_actual = st.session_state.get("usuario_actual")

    st.header(f"3️⃣ Despacho y Control de Topes de Viáticos — {empresa}")

    personal = client.table("personal").select("id_personal, nombre_completo, art_estado").eq(
        "id_empresa", id_empresa
    ).execute().data
    if not personal:
        st.info("Cargá primero legajos de personal en el módulo Contractual y RRHH.")
        return

    nombre_a_persona = {p["nombre_completo"]: p for p in personal}

    with st.form("form_despacho"):
        c1, c2 = st.columns(2)
        operario = c1.selectbox("Operario", list(nombre_a_persona.keys()))
        valor_mo_tarea = c2.number_input("Valor Mano de Obra de la tarea (LPU, ARS)", min_value=0.0)

        c3, c4 = st.columns(2)
        localidad_partida = c3.text_input("Localidad de partida")
        localidad_destino = c4.text_input("Localidad a viajar")

        st.markdown("**Kilometraje**")
        c5, c6, c7 = st.columns(3)
        km_totales = c5.number_input("KMs totales del traslado", min_value=0.0)
        km_franquicia = c6.number_input("Franquicia fija (km)", min_value=0.0, value=FRANQUICIA_KM_DEFAULT)
        valor_referencia_km = c7.number_input(
            "Valor ARS/km (tarifa de referencia del mes)", min_value=0.0,
            help="Tomar de la hoja 'REFERENCIA' del Excel de Desarraigo vigente (ej. 'REFERENCIA Jun.26').",
        )

        st.markdown("**Viáticos por técnico/día**")
        c8, c9, c10 = st.columns(3)
        desayuno = c8.number_input("Desayuno", min_value=0.0)
        almuerzo = c9.number_input("Almuerzo", min_value=0.0)
        cena = c10.number_input("Cena", min_value=0.0)
        alojamiento = st.number_input("Alojamiento", min_value=0.0)

        submitted = st.form_submit_button("Calcular y validar tope", type="primary")

    if submitted:
        persona = nombre_a_persona[operario]
        if persona["art_estado"] in ("vencido", "rechazado"):
            st.error(
                f"🔴 Semáforo Exactian: {operario} tiene ART {persona['art_estado']}. "
                "No se puede asignar en agenda hasta regularizar la ART."
            )
            st.session_state.pop("_despacho_pendiente", None)
        else:
            km_excedente = max(0.0, km_totales - km_franquicia)
            costo_combustible = km_excedente * valor_referencia_km
            costo_total = costo_combustible + desayuno + almuerzo + cena + alojamiento
            tope_15pct = valor_mo_tarea * 0.15
            bloqueado = costo_total > tope_15pct

            datos = {
                "id_empresa": id_empresa,
                "id_personal": persona["id_personal"],
                "localidad_partida": localidad_partida,
                "localidad_destino": localidad_destino,
                "km_totales": km_totales,
                "km_franquicia": km_franquicia,
                "valor_referencia_km": valor_referencia_km,
                "desayuno": desayuno,
                "almuerzo": almuerzo,
                "cena": cena,
                "alojamiento": alojamiento,
                "valor_mo_tarea": valor_mo_tarea,
                "bloqueado": bloqueado,
            }

            if not bloqueado:
                _guardar_despacho(client, datos)
                st.session_state.pop("_despacho_pendiente", None)
                st.success(
                    f"✅ Dentro del tope contractual. Despacho guardado — "
                    f"Costo total $ {costo_total:,.2f} / Tope $ {tope_15pct:,.2f}."
                )
            else:
                # No se guarda todavía: queda pendiente de aprobación de excepción.
                st.session_state["_despacho_pendiente"] = {
                    **datos,
                    "operario_label": operario,
                    "costo_total": costo_total,
                    "tope_15pct": tope_15pct,
                    "km_excedente": km_excedente,
                    "costo_combustible": costo_combustible,
                }

    # --- Panel de aprobación de excepción (persiste entre reruns) ---
    pendiente = st.session_state.get("_despacho_pendiente")
    if pendiente:
        st.divider()
        st.error(
            f"🚫 El despacho de **{pendiente['operario_label']}** SUPERA el tope contractual del 15% "
            f"de la MO — Costo total $ {pendiente['costo_total']:,.2f} vs. Tope $ {pendiente['tope_15pct']:,.2f}. "
            "NO se guardó todavía."
        )
        col1, col2 = st.columns(2)
        col1.metric("KMs excedentes tarifados", f"{pendiente['km_excedente']:,.0f}")
        col2.metric("Costo combustible", f"$ {pendiente['costo_combustible']:,.2f}")

        puede_aprobar = rol_actual == "dueño"
        c_ap, c_desc = st.columns(2)

        if c_ap.button(
            "✅ Aprobar excepción de tope",
            type="primary",
            disabled=not puede_aprobar,
            help=None if puede_aprobar else "Solo un usuario con rol 'dueño' puede aprobar esta excepción.",
        ):
            datos_finales = {
                k: v
                for k, v in pendiente.items()
                if k not in ("operario_label", "costo_total", "tope_15pct", "km_excedente", "costo_combustible")
            }
            datos_finales["aprobado_excepcion"] = True
            datos_finales["aprobado_por"] = usuario_actual
            _guardar_despacho(client, datos_finales)
            st.session_state.pop("_despacho_pendiente")
            st.success(f"Excepción aprobada por {usuario_actual}. Despacho guardado con auditoría.")
            st.rerun()

        if not puede_aprobar:
            st.caption("🔒 Tu rol actual no permite aprobar excepciones al tope de viáticos.")

        if c_desc.button("❌ Descartar despacho"):
            st.session_state.pop("_despacho_pendiente")
            st.rerun()

    st.divider()
    st.subheader("Historial de despachos")
    historial = client.table("despachos_viaticos").select("*").eq(
        "id_empresa", id_empresa
    ).order("fecha", desc=True).execute().data
    if historial:
        st.dataframe(pd.DataFrame(historial), use_container_width=True)
