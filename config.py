"""
config.py
Conexión centralizada a Supabase. Todo módulo que necesite la DB importa
`get_client()` desde acá — nunca crea su propio cliente.
"""
import streamlit as st
from supabase import create_client, Client

EMPRESAS_DISPONIBLES = ["EONFIBER SRL", "SECOON S.R.L.", "ABACOON S.R.L."]

ALMACEN_POR_PREFIJO = {
    "M513": "Instalaciones",
    "C250": "Obras",
    "M250": "Mantenimiento",
    "P250": "Postes",
}


@st.cache_resource
def get_client() -> Client:
    """Crea (una sola vez por sesión de servidor) el cliente de Supabase."""
    try:
        url = st.secrets["supabase"]["url"]
        key = st.secrets["supabase"]["key"]
    except (KeyError, FileNotFoundError):
        st.error(
            "Faltan credenciales de Supabase. Configurá `st.secrets['supabase']['url']` "
            "y `st.secrets['supabase']['key']` en .streamlit/secrets.toml (local) o en "
            "Settings → Secrets (Streamlit Cloud)."
        )
        st.stop()
    return create_client(url, key)


def get_empresa_activa() -> str | None:
    """Lee la Razón Social (texto) seleccionada en la Home. Ninguna pantalla debe
    consultar la DB sin este filtro."""
    return st.session_state.get("empresa_activa")


def require_empresa_activa():
    """Guard para usar al inicio de cada página/módulo."""
    if not get_empresa_activa():
        st.warning("Seleccioná primero una Razón Social en la Home.")
        st.stop()


@st.cache_data(ttl=300)
def _resolver_id_empresa(razon_social: str) -> int:
    """Consulta real a Supabase. Cacheada 5 min porque el mapeo
    razon_social -> id_empresa es prácticamente estático (3 filas fijas)."""
    client = get_client()
    res = (
        client.table("empresas")
        .select("id_empresa")
        .eq("razon_social", razon_social)
        .single()
        .execute()
    )
    if not res.data:
        raise ValueError(f"No existe la empresa '{razon_social}' en la tabla empresas.")
    return res.data["id_empresa"]


def get_id_empresa_activa() -> int:
    """Punto único de conversión string -> int (id_empresa serial de la DB).

    Todo módulo que necesite id_empresa debe llamar a ESTA función en vez de
    reimplementar el lookup contra 'empresas'. Evita el desface de tipos
    (int vs text) al armar filtros .eq('id_empresa', ...) en Supabase, y evita
    tener 7 copias del mismo SELECT desincronizables.
    """
    require_empresa_activa()
    razon_social = get_empresa_activa()
    try:
        return _resolver_id_empresa(razon_social)
    except ValueError as e:
        st.error(str(e))
        st.stop()
