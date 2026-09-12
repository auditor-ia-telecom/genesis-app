-- =====================================================================
-- GENESIS 1.0 - Schema PostgreSQL (Supabase)
-- Contratista de infraestructura de telecomunicaciones (Telecom Argentina)
-- Multi-empresa: EONFIBER SRL / SECOON S.R.L. / ABACOON S.R.L.
-- Basado en documentación real: NPA 7600xxxxx, LPU, Actas de Certificación,
-- Remitos de almacén (M513/C250/M250/P250), Partes Diarios, Pedidos ARIBA 7800xxxxx
-- =====================================================================

-- ---------------------------------------------------------------------
-- 0) EMPRESAS (contexto multi-tenant)
-- ---------------------------------------------------------------------
create table if not exists empresas (
    id_empresa      serial primary key,
    razon_social    text not null unique,       -- 'EONFIBER SRL' | 'SECOON S.R.L.' | 'ABACOON S.R.L.'
    cuit            text,
    segmento        text,                       -- 'Instalaciones' | 'Obras' | 'Mantenimiento/Postes'
    activo          boolean default true,
    created_at      timestamptz default now()
);

insert into empresas (razon_social, segmento) values
    ('EONFIBER SRL', 'Instalaciones'),
    ('SECOON S.R.L.', 'Obras'),
    ('ABACOON S.R.L.', 'Mantenimiento/Postes')
on conflict (razon_social) do nothing;

-- ---------------------------------------------------------------------
-- 1) GESTION CONTRACTUAL Y RRHH (Exactian)
-- ---------------------------------------------------------------------
create table if not exists personal (
    id_personal     serial primary key,
    id_empresa      int not null references empresas(id_empresa),
    nombre_completo text not null,              -- ej. 'NIEVA JONATHAN', 'FERNANDEZ YOEL'
    dni             text,
    cuit            text,
    categoria       text,                       -- oficial, ayudante, sobrestante, jefe de obra, etc.
    fecha_ingreso   date,
    -- Semáforo Exactian (ART)
    art_estado      text check (art_estado in ('vigente','vencido','rechazado','pendiente')) default 'pendiente',
    art_vencimiento date,
    activo          boolean default true,
    created_at      timestamptz default now()
);
create index if not exists idx_personal_empresa on personal(id_empresa);

create table if not exists contratos_npa (
    id_contrato       serial primary key,
    id_empresa        int not null references empresas(id_empresa),
    numero_npa        text not null,             -- patrón 7600... (ej 7600040877)
    proveedor_cuit    text,
    proveedor_nombre  text,
    fecha_emision     date,
    fecha_inicio      date,
    fecha_fin         date,
    monto_maximo      numeric(16,2),
    moneda            text default 'ARS',
    condicion_pago    text,
    pdf_origen        text,                      -- nombre archivo fuente para trazabilidad
    created_at        timestamptz default now(),
    unique (id_empresa, numero_npa)
);

-- Lista de Precios Única (LPU): valor base + índice de actualización (80% IVS + 20% IPC)
create table if not exists lpu_vigencias (
    id_vigencia     serial primary key,
    fecha_vigencia  date not null,
    factor_ivs      numeric(8,5),               -- % variación IVS del período
    factor_ipc      numeric(8,5),               -- % variación IPC del período
    factor_combinado numeric(8,5) generated always as
        (round((0.80 * coalesce(factor_ivs,0) + 0.20 * coalesce(factor_ipc,0))::numeric, 5)) stored,
    created_at      timestamptz default now()
);

create table if not exists lpu_items (
    id_item             serial primary key,
    id_vigencia         int references lpu_vigencias(id_vigencia),
    codigo_sap_s4       text not null,           -- columna 'S4' del excel LPU
    codigo_legacy       text,                    -- 'CÓDIGO' / cod SAP exTeco / exCable
    descripcion         text not null,
    udm                 text,                    -- UN, M, M2, etc.
    tipo_lpu            text,                    -- 'Mantenimiento' | 'Obras' (columna Standard)
    precio_mantenimiento numeric(16,2),
    precio_obras        numeric(16,2),
    created_at          timestamptz default now(),
    unique (id_vigencia, codigo_sap_s4)
);

-- ---------------------------------------------------------------------
-- 2) MODULO LOGISTICO OCR/IA
-- ---------------------------------------------------------------------
create table if not exists almacenes (
    id_almacen   serial primary key,
    id_empresa   int not null references empresas(id_empresa),
    codigo       text not null,                  -- 'M513' | 'C250' | 'M250' | 'P250'
    nombre       text,                            -- Instalaciones / Obras / Mantenimiento / Postes
    unique (id_empresa, codigo)
);

create table if not exists materiales (
    id_material   serial primary key,
    codigo_sap    text not null unique,           -- 'Catálogo' del vale (ej 110100372)
    descripcion   text not null,
    udm           text,
    serializable  boolean default false
);

create table if not exists stock_actual (
    id_almacen   int not null references almacenes(id_almacen),
    id_material  int not null references materiales(id_material),
    cantidad     numeric(14,3) not null default 0,
    primary key (id_almacen, id_material)
);

create table if not exists movimientos_stock (
    id_movimiento    serial primary key,
    id_empresa       int not null references empresas(id_empresa),
    id_almacen       int not null references almacenes(id_almacen),
    id_material      int not null references materiales(id_material),
    tipo_movimiento  text check (tipo_movimiento in ('ingreso','salida')) not null,
    cantidad         numeric(14,3) not null,
    elemento_pep     text,                        -- extraído del encabezado del documento OCR
    documento_origen text,                         -- Nro de remito / vale
    fecha            date not null default current_date,
    origen_archivo   text,                         -- nombre del PDF/escaneo procesado por OCR
    created_at       timestamptz default now()
);
create index if not exists idx_mov_stock_empresa on movimientos_stock(id_empresa);
create index if not exists idx_mov_stock_pep on movimientos_stock(elemento_pep);

create table if not exists vales_salida (
    id_vale       serial primary key,
    id_empresa    int not null references empresas(id_empresa),
    id_almacen    int not null references almacenes(id_almacen),
    id_personal   int references personal(id_personal),  -- operario pre-cargado (match contra legajos), NULLABLE
    -- Campos de texto libre: en la calle el vale se completa a mano y las
    -- liquidaciones de Telecom identifican al técnico por su código WFX
    -- (ej. '63CAD220', visto en OCTUBRE 2025 - EONFIBER.xlsx / hoja RESUMEN),
    -- no siempre por su legajo interno. Se guardan igual aunque exista
    -- id_personal, para no perder trazabilidad contra la liquidación real.
    tecnico_nombre_manuscrito text,
    tecnico_id_wfx            text,
    fecha         date not null default current_date,
    estado        text default 'emitido',
    created_at    timestamptz default now()
);
create index if not exists idx_vales_salida_wfx on vales_salida(tecnico_id_wfx);

create table if not exists vale_items (
    id_vale_item  serial primary key,
    id_vale       int not null references vales_salida(id_vale) on delete cascade,
    id_material   int not null references materiales(id_material),
    cantidad      numeric(14,3) not null
);

-- ---------------------------------------------------------------------
-- 3) DESPACHO Y CONTROL DE TOPES DE VIATICOS (Desarraigo)
-- ---------------------------------------------------------------------
create table if not exists despachos_viaticos (
    id_despacho        serial primary key,
    id_empresa         int not null references empresas(id_empresa),
    id_personal        int references personal(id_personal),
    fecha              date not null default current_date,
    localidad_partida  text,
    localidad_destino  text,
    km_totales         numeric(10,2) default 0,
    km_franquicia      numeric(10,2) default 80,      -- franquicia fija (editable por si Telecom la cambia)
    km_excedente       numeric(10,2) generated always as
        (greatest(coalesce(km_totales,0) - coalesce(km_franquicia,0), 0)) stored,
    valor_referencia_km numeric(12,2) default 0,       -- ARS/km vigente (ref. mensual, ver hoja 'REFERENCIA' del Excel Desarraigo)
    desayuno           numeric(14,2) default 0,
    almuerzo           numeric(14,2) default 0,
    cena               numeric(14,2) default 0,
    alojamiento        numeric(14,2) default 0,
    -- Costo de desarraigo = (KMs totales - franquicia de 80km) * valor ref. por km  +  comidas + alojamiento.
    -- Nota Postgres: un STORED generated column no puede referenciar OTRO generated
    -- column (km_excedente), por eso la resta se repite acá en vez de reusarlo.
    costo_viatico_total numeric(14,2) generated always as (
        (greatest(coalesce(km_totales,0) - coalesce(km_franquicia,0), 0) * coalesce(valor_referencia_km,0))
        + coalesce(desayuno,0) + coalesce(almuerzo,0) + coalesce(cena,0) + coalesce(alojamiento,0)
    ) stored,
    valor_mo_tarea     numeric(14,2),              -- Mano de Obra de la tarea asociada (LPU)
    tope_15pct_mo      numeric(14,2) generated always as (coalesce(valor_mo_tarea,0) * 0.15) stored,
    bloqueado          boolean default false,       -- true si supera el tope
    -- Gobierno de excepciones: un despacho bloqueado puede guardarse igual
    -- SOLO si un usuario con rol 'dueño' lo aprueba explícitamente (ver
    -- app.py / modules/despacho_viaticos.py). Queda trazado quién aprobó.
    aprobado_excepcion boolean default false,
    aprobado_por       text,
    created_at         timestamptz default now()
);

-- ---------------------------------------------------------------------
-- 4) PARTE DIARIO DE OBRA
-- ---------------------------------------------------------------------
create table if not exists partes_diarios (
    id_parte       serial primary key,
    id_empresa     int not null references empresas(id_empresa),
    fecha          date not null,
    proyecto       text,                          -- ej. 'ARATO-26086.AND1-Expansion FTTH'
    zona           text,                           -- ej. 'NAP AA-14'
    porcentaje_avance numeric(5,2),
    origen_archivo text,
    created_at     timestamptz default now()
);

create table if not exists parte_cuadrillas (
    id_cuadrilla   serial primary key,
    id_parte       int not null references partes_diarios(id_parte) on delete cascade,
    equipo_nombre  text,                           -- 'Equipo 1', 'Equipo 8'
    actividad      text,                           -- Postación / Despliegue / Empalmes
    patente        text,
    cantidad_operarios int
);

create table if not exists parte_operarios (
    id_parte_operario serial primary key,
    id_cuadrilla      int not null references parte_cuadrillas(id_cuadrilla) on delete cascade,
    id_personal       int references personal(id_personal),
    nombre_libre      text                          -- fallback si no matchea contra personal
);

-- ---------------------------------------------------------------------
-- 5) CONCILIACION MENSUAL DE INSTALACIONES (Triple Match)
-- ---------------------------------------------------------------------
create table if not exists conciliacion_instalaciones (
    id_conciliacion   serial primary key,
    id_empresa        int not null references empresas(id_empresa),
    periodo           text not null,               -- '2026-09'
    nro_ot            text not null,
    codigo_sap_s4     text,
    id_personal       int references personal(id_personal),
    importe_telecom   numeric(14,2),                -- IMPORTE del CSV de Telecom
    importe_cierre_tecnico numeric(14,2),           -- cierre propio
    estado_match      text check (estado_match in ('coincide','desvio','sin_cruzar')) default 'sin_cruzar',
    fecha_carga       timestamptz default now(),
    unique (id_empresa, periodo, nro_ot)
);

-- ---------------------------------------------------------------------
-- 6) AFIP, ARIBA Y TABLA MAESTRA DE ASOCIACION
-- ---------------------------------------------------------------------

-- 6.A Actas de Certificación (ACAT)
create table if not exists actas_certificacion (
    id_acta            serial primary key,
    id_empresa         int not null references empresas(id_empresa),
    nro_acta           text not null,               -- 'CERTIF. N°'
    obra_tarea         text,                         -- 'FTTH ANDALGALA - Postes'
    pep_oc             text,                         -- 'ARATO-26086.PO'
    sobrestante        text,
    fecha_inicio_obra  date,
    fecha_fin_obra     date,
    monto_certificado  numeric(16,2),
    estado             text check (estado in
        ('pre_certificado','afip_cargado','aceptado_pendiente_cobro','cobrado')) default 'pre_certificado',
    origen_archivo     text,
    created_at         timestamptz default now(),
    unique (id_empresa, nro_acta)
);

create table if not exists acta_items_lpu (
    id_acta_item   serial primary key,
    id_acta        int not null references actas_certificacion(id_acta) on delete cascade,
    codigo_sap_s4  text,
    descripcion    text,
    udm            text,
    cantidad       numeric(14,3),
    precio_unitario numeric(16,2),
    total_certificado numeric(16,2)
);

-- 6.B Datos AFIP de facturación asociados al Acta
create table if not exists facturas_afip (
    id_factura      serial primary key,
    id_acta         int references actas_certificacion(id_acta),
    id_empresa      int not null references empresas(id_empresa),
    nro_factura     text not null,
    cae             text,
    fecha_vencimiento_cae date,
    importe         numeric(16,2),
    created_at      timestamptz default now()
);

-- 6.C Tabla Maestra de Asociación (ARIBA <-> NPA <-> Acta)
create table if not exists tabla_maestra_pedidos (
    id_pedido           serial primary key,
    id_empresa          int not null references empresas(id_empresa),
    numero_pedido_ariba  text not null,             -- patrón 7800... (ej 7800420385)
    numero_npa           text,                       -- patrón 7600... (ej 7600040725)
    id_acta              int references actas_certificacion(id_acta),
    pep_oc               text,
    importe              numeric(16,2),
    estado               text default 'vinculado',
    origen_archivo        text,
    created_at            timestamptz default now(),
    unique (id_empresa, numero_pedido_ariba)
);

-- 6.D Reporte ARIBA (HES / WE) enlazado a la Tabla Maestra
create table if not exists ariba_hes_we (
    id_hes_we      serial primary key,
    id_pedido      int not null references tabla_maestra_pedidos(id_pedido) on delete cascade,
    nro_hes        text,
    nro_we         text,
    importe        numeric(16,2),
    fecha_reporte  date,
    origen_archivo text,
    created_at     timestamptz default now()
);

-- ---------------------------------------------------------------------
-- Vistas de apoyo para el Home (KPIs)
-- ---------------------------------------------------------------------
create or replace view vw_kpi_home as
select
    e.id_empresa,
    e.razon_social,
    (select count(*) from tabla_maestra_pedidos p where p.id_empresa = e.id_empresa and p.estado <> 'cobrado') as pedidos_activos,
    (select count(*) from conciliacion_instalaciones c where c.id_empresa = e.id_empresa and c.estado_match = 'desvio') as desvios_alerta,
    (select coalesce(sum(a.monto_certificado),0) from actas_certificacion a where a.id_empresa = e.id_empresa and a.estado = 'pre_certificado') as total_prefacturado,
    (select coalesce(sum(p.importe),0) from tabla_maestra_pedidos p where p.id_empresa = e.id_empresa and p.estado = 'vinculado') as total_ariba,
    -- Retenido en Exactian: por definición del Ing. Loyola, monto certificado
    -- que aún está en estado 'pre_certificado' (aún no pasó por AFIP/ARIBA).
    -- OJO: con esta definición literal, retenido_exactian == total_prefacturado
    -- (mismo criterio exacto). Se deja como columna separada para que el día
    -- que Exactian tenga fuente propia (ej. una tabla exactian_retenciones)
    -- alcance con cambiar este SELECT sin tocar el resto de la vista ni el Home.
    (select coalesce(sum(a.monto_certificado),0) from actas_certificacion a where a.id_empresa = e.id_empresa and a.estado = 'pre_certificado') as retenido_exactian
from empresas e;

create or replace view vw_antiguedad_facturas as
select
    f.id_empresa,
    e.razon_social,
    f.id_factura,
    f.importe,
    (current_date - f.fecha_vencimiento_cae) as dias_vencido,
    case
        when (current_date - f.fecha_vencimiento_cae) <= 30 then '0-30'
        when (current_date - f.fecha_vencimiento_cae) <= 60 then '31-60'
        when (current_date - f.fecha_vencimiento_cae) <= 90 then '61-90'
        else '90+'
    end as bucket_vencimiento
from facturas_afip f
join empresas e on e.id_empresa = f.id_empresa;

-- =====================================================================
-- ROW LEVEL SECURITY — desactivado a propósito para el MVP v1.0
-- =====================================================================
-- Decisión del Ing. Cristian Loyola: uso interno, de confianza, sin
-- Supabase Auth todavía. Se desactiva RLS en las 21 tablas operativas
-- para que la 'anon key' pueda hacer select/insert/update/delete
-- directamente desde Streamlit sin que Supabase las bloquee por defecto.
--
-- Esto NO es lo mismo que "seguro": cualquiera con la URL del proyecto y
-- la anon key (ambas quedan en st.secrets del lado servidor de Streamlit,
-- no viajan al navegador del usuario final) tiene lectura y escritura total
-- sobre las 3 empresas, sin distinción de usuario ni de rol. Server-side
-- está razonablemente protegido mientras el secrets.toml no se filtre; el
-- riesgo real es de autorización, no de exposición del secreto en sí.
-- Pendiente para v1.1: Supabase Auth + políticas por id_empresa/rol.
-- =====================================================================

alter table empresas                    disable row level security;
alter table personal                    disable row level security;
alter table contratos_npa               disable row level security;
alter table lpu_vigencias               disable row level security;
alter table lpu_items                   disable row level security;
alter table almacenes                   disable row level security;
alter table materiales                  disable row level security;
alter table stock_actual                disable row level security;
alter table movimientos_stock           disable row level security;
alter table vales_salida                disable row level security;
alter table vale_items                  disable row level security;
alter table despachos_viaticos          disable row level security;
alter table partes_diarios              disable row level security;
alter table parte_cuadrillas            disable row level security;
alter table parte_operarios             disable row level security;
alter table conciliacion_instalaciones  disable row level security;
alter table actas_certificacion         disable row level security;
alter table acta_items_lpu              disable row level security;
alter table facturas_afip               disable row level security;
alter table tabla_maestra_pedidos       disable row level security;
alter table ariba_hes_we                disable row level security;

