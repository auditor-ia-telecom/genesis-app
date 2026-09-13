"""
modules/logistica.py — Módulo 2: Carga logística + Vale de Salida Interno.

Flujo:
  1. st.file_uploader recibe el PDF/escaneo del remito.
  2. utils.ocr_parser detecta el prefijo (M513/C250/M250/P250) -> tipo de almacén.
  3. Se muestra al usuario una preview editable de los ítems detectados
     antes de confirmar el ingreso a stock (nunca se escribe a ciegas).
  4. Aparte, la pestaña "Vale de Salida" arma el vale interno para un operario.
"""
import streamlit as st
import pandas as pd
import math
from datetime import datetime, timezone

from config import get_client, get_empresa_activa, get_id_empresa_activa, ALMACEN_POR_PREFIJO
from utils.ocr_parser import procesar_remito


def _limpio(valor, default=None):
    """Convierte NaN/None (típico de celdas vacías en st.data_editor) a un
    valor por defecto ANTES de mandarlo a Supabase.

    Ojo con 'valor or default': falla con NaN porque NaN es "truthy" en
    Python (nan or x == nan, no x) — por eso NaN se colaba tal cual hasta la
    llamada HTTP, y json.dumps(allow_nan=False) la rechaza con un
    ValueError bastante críptico ("Out of range float values...").
    """
    if valor is None:
        return default
    try:
        if isinstance(valor, float) and math.isnan(valor):
            return default
        if pd.isna(valor):
            return default
    except (TypeError, ValueError):
        pass
    return valor



def _get_or_create_almacen(client, id_empresa: int, tipo_almacen: str, codigo: str) -> int:
    existente = (
        client.table("almacenes")
        .select("id_almacen")
        .eq("id_empresa", id_empresa)
        .eq("codigo", codigo)
        .execute()
        .data
    )
    if existente:
        return existente[0]["id_almacen"]
    nuevo = client.table("almacenes").insert(
        {"id_empresa": id_empresa, "codigo": codigo, "nombre": tipo_almacen}
    ).execute()
    return nuevo.data[0]["id_almacen"]


def _get_or_create_material(client, codigo_sap: str, descripcion: str, udm: str) -> int:
    descripcion = _limpio(descripcion, "(sin descripción)")
    existente = client.table("materiales").select("id_material").eq("codigo_sap", codigo_sap).execute().data
    if existente:
        return existente[0]["id_material"]
    nuevo = client.table("materiales").insert(
        {"codigo_sap": codigo_sap, "descripcion": descripcion, "udm": udm}
    ).execute()
    return nuevo.data[0]["id_material"]


def _remitos_ya_cargados(client, id_empresa: int, numeros_documento: set[str]) -> dict[str, str]:
    """Devuelve {numero_documento: fecha_carga} para los documentos de esa
    lista que YA existen en remitos_ingresados para esta empresa."""
    if not numeros_documento:
        return {}
    filas = (
        client.table("remitos_ingresados")
        .select("numero_documento, fecha_carga")
        .eq("id_empresa", id_empresa)
        .in_("numero_documento", list(numeros_documento))
        .execute()
        .data
    )
    return {f["numero_documento"]: f["fecha_carga"] for f in filas}


def _tab_carga_documentos(client, id_empresa: int):
    st.subheader("Carga de remitos / OCR")
    archivo = st.file_uploader(
        "Subí el PDF o la foto del remito (M513 / C250 / M250 / P250)",
        type=["pdf", "jpg", "jpeg", "png"],
        help="Una foto sacada con el celular funciona, pero el OCR sale mejor cuanto más plana, "
        "derecha y bien iluminada esté la toma — evitá sombras y perspectiva torcida si podés.",
    )

    if archivo is None:
        return

    with st.spinner("Extrayendo texto (OCR si es escaneo) y detectando ítems..."):
        resultado = procesar_remito(archivo.read())

    # El texto crudo se muestra SIEMPRE, haya o no ítems detectados — antes
    # solo aparecía cuando el parseo fallaba del todo, así que si detectaba
    # ALGO (aunque incompleto) no había forma de ver qué leyó Tesseract.
    with st.expander("🔍 Texto crudo (debug OCR)", expanded=not resultado.items):
        st.text(resultado.texto_crudo or "(vacío)")

    if not resultado.tipo_almacen:
        st.error(
            "No se detectó ningún prefijo conocido (M513/C250/M250/P250) en el documento. "
            "Revisá el archivo o cargá el Elemento PEP manualmente."
        )
        prefijo_manual = st.selectbox("Elemento PEP manual", list(ALMACEN_POR_PREFIJO.keys()))
        tipo_almacen = ALMACEN_POR_PREFIJO[prefijo_manual]
        codigo = prefijo_manual
    else:
        tipo_almacen = resultado.tipo_almacen
        codigo = [k for k, v in ALMACEN_POR_PREFIJO.items() if v == tipo_almacen][0]
        st.success(f"Detectado: almacén de **{tipo_almacen}** ({codigo})")

    if not resultado.items:
        st.warning("No se pudieron extraer ítems automáticamente. Revisá el texto crudo arriba.")
        return

    df_items = pd.DataFrame([i.__dict__ for i in resultado.items])

    # --- Detección de remitos ya cargados (por N° de Documento) ---
    documentos_en_pdf = set(df_items["numero_documento"].dropna().unique())
    ya_cargados = _remitos_ya_cargados(client, id_empresa, documentos_en_pdf)

    forzar_recarga = False
    if ya_cargados:
        detalle = "\n".join(f"- **{doc}** (cargado el {fecha})" for doc, fecha in ya_cargados.items())
        st.error(
            f"🚫 {len(ya_cargados)} de los remitos de este PDF YA fueron cargados antes:\n\n{detalle}\n\n"
            "Sus ítems NO se muestran en la grilla de abajo para evitar duplicar stock."
        )
        forzar_recarga = st.checkbox(
            "⚠️ Sé que ya se cargaron y quiero incluirlos de todos modos (uso excepcional, ej. corrección)",
            value=False,
        )

    if forzar_recarga:
        df_a_mostrar = df_items
    else:
        df_a_mostrar = df_items[~df_items["numero_documento"].isin(ya_cargados.keys())].reset_index(drop=True)

    if df_a_mostrar.empty:
        st.info("No queda ningún ítem nuevo para cargar de este PDF (todos sus remitos ya estaban cargados).")
        return

    cantidad_revision = int(df_a_mostrar["necesita_revision"].sum()) if "necesita_revision" in df_a_mostrar else 0
    if cantidad_revision:
        st.warning(
            f"⚠️ {cantidad_revision} ítem(s) quedaron marcados como 'necesita_revision' porque el OCR no pudo "
            "leer la cantidad con confianza (falta la unidad de medida, o el número quedó en otro renglón del "
            "escaneo). Corregí la cantidad a mano en la grilla contra el PDF original antes de confirmar."
        )

    st.write("Ítems detectados (editables antes de confirmar):")
    df_editada = st.data_editor(
        df_a_mostrar,
        num_rows="dynamic",
        use_container_width=True,
        column_config={
            "necesita_revision": st.column_config.CheckboxColumn(
                "¿Revisar?", help="Marcado automáticamente cuando el OCR no pudo leer la cantidad con confianza."
            ),
            "numero_documento": st.column_config.TextColumn(
                "N° Documento (remito)", help="Identificador del remito — se usa para detectar duplicados."
            ),
        },
    )

    # Filas agregadas a mano con el "+" del data_editor NUNCA pasaron por el
    # parser, así que 'necesita_revision' no las cubre. Se valida acá aparte,
    # sobre la grilla ya editada, para no dejar pasar cantidad/catálogo en
    # blanco (que en pandas es NaN, no None) a la confirmación.
    columnas_obligatorias = ["catalogo", "cantidad", "udm"]
    filas_incompletas = df_editada[columnas_obligatorias].isna().any(axis=1)

    if df_editada["necesita_revision"].any() or filas_incompletas.any():
        st.error(
            "🚫 Hay ítems marcados para revisar, o filas con catálogo/cantidad/UM en blanco "
            "(incluidas filas agregadas a mano). Completalos en la grilla antes de confirmar "
            "el ingreso a stock."
        )

    if st.button(
        "✅ Confirmar ingreso a stock",
        type="primary",
        disabled=bool(df_editada["necesita_revision"].any() or filas_incompletas.any()),
    ):
        id_almacen = _get_or_create_almacen(client, id_empresa, tipo_almacen, codigo)
        for _, row in df_editada.iterrows():
            id_material = _get_or_create_material(client, row["catalogo"], row["descripcion"], row["udm"])
            cantidad_limpia = _limpio(row["cantidad"], 0)
            client.table("movimientos_stock").insert(
                {
                    "id_empresa": id_empresa,
                    "id_almacen": id_almacen,
                    "id_material": id_material,
                    "tipo_movimiento": "ingreso",
                    "cantidad": cantidad_limpia,
                    "elemento_pep": _limpio(row.get("elemento_pep"), codigo),
                    "documento_origen": _limpio(row.get("numero_documento")),
                    "origen_archivo": archivo.name,
                }
            ).execute()
            # upsert de stock_actual
            actual = (
                client.table("stock_actual")
                .select("cantidad")
                .eq("id_almacen", id_almacen)
                .eq("id_material", id_material)
                .execute()
                .data
            )
            nueva_cantidad = cantidad_limpia + (actual[0]["cantidad"] if actual else 0)
            client.table("stock_actual").upsert(
                {"id_almacen": id_almacen, "id_material": id_material, "cantidad": nueva_cantidad}
            ).execute()

        # Marco cada N° de Documento confirmado como ya cargado, para que un
        # futuro intento de recarga (sin forzar) quede bloqueado por default.
        documentos_confirmados = set(df_editada["numero_documento"].dropna().unique())
        for numero_documento in documentos_confirmados:
            client.table("remitos_ingresados").upsert(
                {
                    "id_empresa": id_empresa,
                    "id_almacen": id_almacen,
                    "numero_documento": numero_documento,
                    "origen_archivo": archivo.name,
                    "fecha_carga": datetime.now(timezone.utc).isoformat(),
                },
                on_conflict="id_empresa,numero_documento",
            ).execute()

        st.success(f"{len(df_editada)} ítems ingresados al almacén de {tipo_almacen}.")


def _tab_vale_salida(client, id_empresa: int):
    st.subheader("Vale de Salida Interno")
    st.caption(
        "El remito en la calle se completa a mano y la liquidación de Telecom identifica "
        "al técnico por su código WFX (ej. 63CAD220), no siempre por el legajo interno. "
        "Por eso el operario se puede tipear libre en vez de exigir un match exacto contra RRHH."
    )

    personal = client.table("personal").select("id_personal, nombre_completo").eq(
        "id_empresa", id_empresa
    ).execute().data
    almacenes = client.table("almacenes").select("id_almacen, nombre, codigo").eq(
        "id_empresa", id_empresa
    ).execute().data

    if not almacenes:
        st.info("Necesitás al menos un almacén con stock para emitir un vale.")
        return

    nombre_a_id = {p["nombre_completo"]: p["id_personal"] for p in personal} if personal else {}
    almacen_a_id = {f"{a['nombre']} ({a['codigo']})": a["id_almacen"] for a in almacenes}

    modo_carga = st.radio(
        "¿Cómo identificás al técnico?",
        ["Seleccionar de legajos (RRHH)", "Carga manuscrita / código WFX"],
        horizontal=True,
    )

    id_personal_sel = None
    tecnico_nombre_manuscrito = None
    tecnico_id_wfx = None

    if modo_carga == "Seleccionar de legajos (RRHH)" and nombre_a_id:
        operario_sel = st.selectbox("Operario (pre-cargado)", list(nombre_a_id.keys()))
        id_personal_sel = nombre_a_id[operario_sel]
        operario_label = operario_sel
    else:
        if modo_carga == "Seleccionar de legajos (RRHH)":
            st.warning("Todavía no hay legajos cargados en RRHH — cargá el vale como manuscrito/WFX.")
        c1, c2 = st.columns(2)
        tecnico_nombre_manuscrito = c1.text_input("Nombre del técnico (tal como figura en el remito)")
        tecnico_id_wfx = c2.text_input("Código WFX (ej. 63CAD220)")
        operario_label = tecnico_nombre_manuscrito or tecnico_id_wfx or "(sin identificar)"

    almacen_sel = st.selectbox("Almacén", list(almacen_a_id.keys()))
    id_almacen = almacen_a_id[almacen_sel]

    materiales_stock = (
        client.table("stock_actual")
        .select("cantidad, materiales(id_material, codigo_sap, descripcion, udm)")
        .eq("id_almacen", id_almacen)
        .execute()
        .data
    )
    if not materiales_stock:
        st.warning("Este almacén no tiene stock cargado todavía.")
        return

    opciones = {
        f"{m['materiales']['codigo_sap']} — {m['materiales']['descripcion']} (stock: {m['cantidad']})": m
        for m in materiales_stock
    }
    seleccionados = st.multiselect("Ítems a retirar", list(opciones.keys()))

    cantidades = {}
    for sel in seleccionados:
        cantidades[sel] = st.number_input(f"Cantidad — {sel}", min_value=0.0, step=1.0, key=f"cant_{sel}")

    identificacion_incompleta = id_personal_sel is None and not (tecnico_nombre_manuscrito or tecnico_id_wfx)
    if identificacion_incompleta:
        st.warning("Completá al menos el nombre manuscrito o el código WFX antes de emitir el vale.")

    if st.button(
        "🖨️ Emitir Vale de Salida",
        type="primary",
        disabled=not seleccionados or identificacion_incompleta,
    ):
        vale = client.table("vales_salida").insert(
            {
                "id_empresa": id_empresa,
                "id_almacen": id_almacen,
                "id_personal": id_personal_sel,
                "tecnico_nombre_manuscrito": tecnico_nombre_manuscrito,
                "tecnico_id_wfx": tecnico_id_wfx,
            }
        ).execute()
        id_vale = vale.data[0]["id_vale"]

        for sel, cantidad in cantidades.items():
            item = opciones[sel]
            id_material = item["materiales"]["id_material"]
            client.table("vale_items").insert(
                {"id_vale": id_vale, "id_material": id_material, "cantidad": cantidad}
            ).execute()
            client.table("movimientos_stock").insert(
                {
                    "id_empresa": id_empresa,
                    "id_almacen": id_almacen,
                    "id_material": id_material,
                    "tipo_movimiento": "salida",
                    "cantidad": cantidad,
                    "documento_origen": f"VALE-{id_vale}",
                }
            ).execute()
            nueva_cantidad = item["cantidad"] - cantidad
            client.table("stock_actual").update({"cantidad": nueva_cantidad}).eq(
                "id_almacen", id_almacen
            ).eq("id_material", id_material).execute()

        st.success(f"Vale N° {id_vale} emitido para {operario_label}. Restado del almacén en tiempo real.")


def render():
    client = get_client()
    empresa = get_empresa_activa()
    id_empresa = get_id_empresa_activa()

    st.header(f"2️⃣ Logística OCR/IA — {empresa}")
    tab1, tab2 = st.tabs(["📥 Carga de documentos", "📤 Vale de Salida"])
    with tab1:
        _tab_carga_documentos(client, id_empresa)
    with tab2:
        _tab_vale_salida(client, id_empresa)
