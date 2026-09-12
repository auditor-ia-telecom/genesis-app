"""
app.py — Punto de entrada de Genesis 1.0

Orden de arranque (importante, en este orden exacto):
  0. render_login_screen(): sin sesión autenticada, solo se ve el formulario
     de login; ni get_client() ni ningún módulo corren antes de esto.
  1. Autenticado: se guarda también el ROL del usuario (st.session_state
     ["rol_actual"]), leído de st.secrets — nunca lo elige el usuario.
  2. El usuario elige la Razón Social (empresa_activa).
  3. El sidebar arma la lista de módulos SEGÚN EL ROL (MODULOS_POR_ROL) y
     despacha al módulo elegido. Cada módulo, además, filtra por id_empresa.

Roles soportados:
  - "dueño":        los 6 caminos completos + Home.
  - "coordinador":  todo excepto el módulo 6 (AFIP/ARIBA/Tabla Maestra).
  - "operario":     únicamente el módulo 2 (Logística OCR/IA); sin Home,
                     porque Home expone montos financieros (ARIBA, pre-
                     facturado) que un pañolero no necesita ver.
"""
import hmac
import unicodedata

import streamlit as st
from config import get_client, get_empresa_activa, EMPRESAS_DISPONIBLES

from modules import (
    home,
    contractual_rrhh,
    logistica,
    despacho_viaticos,
    parte_diario,
    conciliacion,
    tabla_maestra,
)

st.set_page_config(page_title="Genesis 1.0", layout="wide", page_icon="📡")

# (clave interna, etiqueta visible en el radio del sidebar)
MODULOS = [
    ("home", "🏠 Home"),
    ("1", "1️⃣ Contractual y RRHH (Exactian)"),
    ("2", "2️⃣ Logística OCR/IA"),
    ("3", "3️⃣ Despacho y Viáticos"),
    ("4", "4️⃣ Parte Diario de Obra"),
    ("5", "5️⃣ Conciliación Instalaciones"),
    ("6", "6️⃣ AFIP / ARIBA / Tabla Maestra"),
]

MODULOS_POR_ROL = {
    "dueño": {"home", "1", "2", "3", "4", "5", "6"},
    "coordinador": {"home", "1", "2", "3", "4", "5"},  # sin el 6
    "operario": {"2"},  # solo Logística; ni Home
}

ROLES_VALIDOS = set(MODULOS_POR_ROL.keys())


def _normalizar(texto: str) -> str:
    """Unicode NFC + strip de espacios en los bordes.

    Copiar credenciales entre GitHub (web), esta conversación y la caja de
    Secrets de Streamlit Cloud puede introducir dos problemas invisibles en
    pantalla: espacios sueltos al principio/final, y variantes de
    normalización Unicode de 'ñ'/tildes (la misma letra puede representarse
    con bytes distintos según por dónde pasó el texto). Ambos hacen que un
    string que se VE idéntico compare distinto byte a byte. Se normaliza acá
    tanto lo que escribe el usuario en el login como lo leído de secrets,
    para que 'dueño' siempre sea 'dueño' sin importar el origen del texto.
    """
    return unicodedata.normalize("NFC", texto).strip()


# ---------------------------------------------------------------------
# 0) LOGIN — gate obligatorio, corre antes que cualquier otra cosa
# ---------------------------------------------------------------------
def _usuarios_validos() -> dict:
    """Lee st.secrets['auth']['usuarios']: dict de
    usuario -> {"password": "...", "rol": "dueño"|"coordinador"|"operario"}.
    """
    try:
        return dict(st.secrets["auth"]["usuarios"])
    except (KeyError, FileNotFoundError):
        st.error(
            "Falta configurar `[auth.usuarios]` en `.streamlit/secrets.toml` (local) o en "
            "Settings → Secrets (Streamlit Cloud). Sin esto la app no puede validar accesos "
            "y por seguridad no se muestra ninguna pantalla más."
        )
        st.stop()


def _validar_credenciales(usuario: str, contrasena: str) -> str | None:
    """Devuelve el ROL si usuario+contraseña son correctos, o None si no.

    La búsqueda del usuario y la comparación de rol se hacen sobre versiones
    normalizadas (ver _normalizar) para tolerar espacios invisibles o
    variantes de codificación de acentos introducidas al copiar/pegar entre
    GitHub y Secrets — sin esto, un typo invisible se ve idéntico en pantalla
    pero rechaza el login igual.
    """
    usuario_norm = _normalizar(usuario) if usuario else usuario
    usuarios = _usuarios_validos()

    datos_usuario = None
    for clave_usuario, datos in usuarios.items():
        if isinstance(clave_usuario, str) and _normalizar(clave_usuario) == usuario_norm:
            datos_usuario = datos
            break

    if not isinstance(datos_usuario, dict):
        return None

    contrasena_esperada = datos_usuario.get("password")
    rol = datos_usuario.get("rol")

    if isinstance(rol, str):
        rol = _normalizar(rol)
    if not isinstance(contrasena_esperada, str) or rol not in ROLES_VALIDOS:
        return None

    contrasena_norm = _normalizar(contrasena) if contrasena else contrasena
    contrasena_esperada_norm = _normalizar(contrasena_esperada)

    # --- DEBUG TEMPORAL: sacar en cuanto el login funcione ---
    # Imprime SOLO a los logs privados de Streamlit Cloud (Manage app → Logs,
    # el panel que solo ve el dueño del deploy). Nunca se muestra en la UI
    # ni queda en ninguna captura de pantalla que se comparta.
    print(
        f"[DEBUG LOGIN] usuario_tipeado={usuario_norm!r} "
        f"clave_encontrada_en_secrets={clave_usuario!r} "
        f"largo_contrasena_tipeada={len(contrasena_norm)} "
        f"largo_contrasena_secrets={len(contrasena_esperada_norm)} "
        f"coinciden_exacto={contrasena_norm == contrasena_esperada_norm} "
        f"rol_leido={rol!r}"
    )
    # --- FIN DEBUG TEMPORAL ---

    if hmac.compare_digest(contrasena_norm, contrasena_esperada_norm):
        return rol
    return None


def render_login_screen():
    st.title("🔒 Genesis 1.0")
    st.caption("Acceso restringido — EONFIBER · SECOON · ABACOON")

    with st.form("form_login"):
        usuario = st.text_input("Usuario")
        contrasena = st.text_input("Contraseña", type="password")
        submitted = st.form_submit_button("Ingresar", type="primary", use_container_width=True)

    if not submitted:
        return

    rol = _validar_credenciales(usuario, contrasena) if usuario else None
    if rol:
        st.session_state["autenticado"] = True
        st.session_state["usuario_actual"] = usuario
        st.session_state["rol_actual"] = rol
        st.rerun()
    else:
        st.error("🚫 Usuario o contraseña incorrectos.")


def esta_autenticado() -> bool:
    return st.session_state.get("autenticado", False) is True


def cerrar_sesion():
    for clave in ("autenticado", "usuario_actual", "rol_actual", "empresa_activa"):
        st.session_state.pop(clave, None)
    st.rerun()


# ---------------------------------------------------------------------
# 1) Selector de empresa + navegación (solo alcanzable ya autenticado)
# ---------------------------------------------------------------------
def render_selector_empresa():
    st.title("📡 Genesis 1.0")
    st.caption(
        f"Panel de gestión — EONFIBER · SECOON · ABACOON — "
        f"sesión: **{st.session_state['usuario_actual']}** ({st.session_state['rol_actual']})"
    )
    st.subheader("Seleccioná la Razón Social")

    cols = st.columns(3)
    for col, empresa in zip(cols, EMPRESAS_DISPONIBLES):
        with col:
            if st.button(empresa, use_container_width=True, type="primary"):
                st.session_state["empresa_activa"] = empresa
                st.rerun()

    st.divider()
    if st.button("🚪 Cerrar sesión"):
        cerrar_sesion()


def render_sidebar_nav() -> str:
    """Arma el radio del sidebar SOLO con los módulos permitidos para el rol
    actual. No es un filtro cosmético: main() vuelve a validar el resultado
    contra MODULOS_POR_ROL antes de despachar, así que aunque alguien
    manipule el estado de sesión, el módulo igual no se ejecuta.
    """
    empresa = get_empresa_activa()
    rol = st.session_state["rol_actual"]

    st.sidebar.success(f"Usuario: **{st.session_state['usuario_actual']}** — rol: **{rol}**")
    st.sidebar.success(f"Empresa activa: **{empresa}**")

    col1, col2 = st.sidebar.columns(2)
    if col1.button("↩️ Cambiar empresa", use_container_width=True):
        del st.session_state["empresa_activa"]
        st.rerun()
    if col2.button("🚪 Salir", use_container_width=True):
        cerrar_sesion()

    st.sidebar.divider()

    claves_permitidas = MODULOS_POR_ROL[rol]
    opciones_visibles = [etiqueta for clave, etiqueta in MODULOS if clave in claves_permitidas]

    if not opciones_visibles:
        st.sidebar.error("Tu rol no tiene módulos asignados. Contactá al administrador.")
        st.stop()

    return st.sidebar.radio("Módulos", opciones_visibles)


def _clave_desde_etiqueta(etiqueta: str) -> str:
    for clave, et in MODULOS:
        if et == etiqueta:
            return clave
    return ""


def main():
    # --- Gate de autenticación: nada de lo de abajo corre sin esto ---
    if not esta_autenticado():
        render_login_screen()
        st.stop()  # corta acá: ni get_client() ni ningún módulo se ejecutan

    get_client()  # valida credenciales de Supabase apenas arranca la app

    if not get_empresa_activa():
        render_selector_empresa()
        return

    etiqueta_elegida = render_sidebar_nav()
    clave = _clave_desde_etiqueta(etiqueta_elegida)
    rol = st.session_state["rol_actual"]

    # Defensa en profundidad: aunque el radio ya viene filtrado, se vuelve a
    # chequear el permiso antes de ejecutar el módulo.
    if clave not in MODULOS_POR_ROL[rol]:
        st.error("🚫 No tenés permiso para acceder a este módulo con tu rol actual.")
        st.stop()

    if clave == "home":
        home.render()
    elif clave == "1":
        contractual_rrhh.render()
    elif clave == "2":
        logistica.render()
    elif clave == "3":
        despacho_viaticos.render()
    elif clave == "4":
        parte_diario.render()
    elif clave == "5":
        conciliacion.render()
    elif clave == "6":
        tabla_maestra.render()


if __name__ == "__main__":
    main()
