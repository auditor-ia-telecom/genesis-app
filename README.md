# Genesis 1.0 — MVP

ERP multi-empresa (EONFIBER SRL / SECOON S.R.L. / ABACOON S.R.L.) para gestión
de contratos, logística, viáticos, partes diarios, conciliación y AFIP/ARIBA
sobre operaciones con Telecom Argentina S.A.

## Stack
- **Frontend/servidor:** Python + Streamlit
- **Base de datos:** Supabase (PostgreSQL, plan free)
- **OCR:** pdfplumber (texto nativo) + pytesseract (fallback para escaneos)

## Puesta en marcha

1. Creá un proyecto en [supabase.com](https://supabase.com) (plan Free).
2. En **SQL Editor**, ejecutá el contenido de `db/schema.sql` completo.
3. Copiá `.streamlit/secrets.toml.example` a `.streamlit/secrets.toml` y
   completá `url` y `key` con los datos de Settings → API de tu proyecto.
4. Local: `pip install -r requirements.txt && streamlit run app.py`
5. Streamlit Cloud: conectá el repo de GitHub, cargá los mismos secrets en
   Settings → Secrets, y verificá que `packages.txt` esté presente (instala
   `tesseract-ocr` en el contenedor — necesario para los PDFs escaneados).

## Estructura

```
genesis_app/
├── app.py                    # Entry point + navegación + selector de empresa
├── config.py                 # Cliente Supabase + helpers de sesión
├── db/schema.sql             # Schema completo multi-empresa
├── modules/
│   ├── home.py               # Dashboard KPIs + 4 widgets gráficos
│   ├── contractual_rrhh.py   # Módulo 1: NPA, LPU, Legajos + Semáforo Exactian
│   ├── logistica.py          # Módulo 2: OCR de remitos + Vale de Salida
│   ├── despacho_viaticos.py  # Módulo 3: Cálculo y tope de desarraigo (15% MO)
│   ├── parte_diario.py       # Módulo 4: Parte Diario de Obra (extracción)
│   ├── conciliacion.py       # Módulo 5: Triple Match Instalaciones
│   └── tabla_maestra.py      # Módulo 6: AFIP / ARIBA / Tabla Maestra
└── utils/
    └── ocr_parser.py         # Detección M513/C250/M250/P250 + parseo de ítems
```

## Decisiones de diseño relevantes

- **Multi-empresa real, no solo un filtro visual:** cada tabla de negocio
  tiene `id_empresa` como FK y todas las queries de cada módulo filtran por
  la empresa activa (`st.session_state["empresa_activa"]`). No hay vista
  que mezcle datos de dos razones sociales.
- **Remitos son PDFs escaneados**, confirmado al abrir los archivos reales
  de Carpeta_2 (sin capa de texto) — por eso `ocr_parser.py` no asume texto
  nativo y cae a Tesseract página por página.
- **LPU indexada:** se modela como `lpu_vigencias` (con el factor 80% IVS +
  20% IPC calculado en la base vía columna generada) + `lpu_items` con precio
  base por vigencia, en vez de guardar un precio "actual" mutable.
- **Tabla Maestra ARIBA↔NPA:** el propio PDF del Pedido de Compra (7800...)
  trae impreso su "Número de contrato" (7600...), por lo que la vinculación
  se extrae directamente por regex del texto del PDF.

## Pendiente / próximos pasos sugeridos

- Ajustar los regex de `ocr_parser.py` y `parte_diario.py` contra una muestra
  más grande de documentos reales (los provistos alcanzan para el layout
  base, pero Telecom podría variar el formato entre regiones).
- Definir la fuente real de "Retenido en Exactian" (KPI dejado como
  placeholder — no vino en la documentación adjunta un archivo de Exactian).
- Agregar autenticación de usuarios (Supabase Auth) antes de exponer la app
  fuera de un uso interno de confianza, ya que hoy cualquiera con la URL
  pública de Streamlit Cloud accede a los 3 contextos empresariales.
