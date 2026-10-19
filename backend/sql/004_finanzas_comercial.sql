CREATE TABLE finanzas.medios_pago (
  codigo text PRIMARY KEY,
  nombre text NOT NULL,
  activo boolean NOT NULL DEFAULT true,
  requiere_confirmacion boolean NOT NULL DEFAULT true
);

CREATE TABLE finanzas.pagos (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  orden_id uuid REFERENCES operacion.ordenes(id),
  cliente_id uuid REFERENCES crm.clientes(id),
  medio text NOT NULL REFERENCES finanzas.medios_pago(codigo),
  tipo text NOT NULL,
  monto numeric(16, 2) NOT NULL,
  estado text NOT NULL DEFAULT 'pendiente',
  referencia text,
  proveedor text,
  metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
  creado_en timestamptz NOT NULL DEFAULT now(),
  confirmado_en timestamptz,
  CONSTRAINT pagos_tipo_chk CHECK (tipo IN ('anticipo', 'saldo', 'total', 'propina', 'reembolso', 'credito')),
  CONSTRAINT pagos_estado_chk CHECK (estado IN ('pendiente', 'confirmado', 'rechazado', 'reembolsado'))
);

CREATE INDEX pagos_orden_idx ON finanzas.pagos (orden_id, creado_en DESC);

CREATE TABLE finanzas.planilla_ingresos (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  orden_id uuid NOT NULL UNIQUE REFERENCES operacion.ordenes(id),
  empleado_id uuid REFERENCES personal.empleados(id),
  precio numeric(16, 2) NOT NULL,
  costo_mano_obra numeric(16, 2) NOT NULL DEFAULT 0,
  cargas numeric(16, 2) NOT NULL DEFAULT 0,
  traslado numeric(16, 2) NOT NULL DEFAULT 0,
  insumos numeric(16, 2) NOT NULL DEFAULT 0,
  propina numeric(16, 2) NOT NULL DEFAULT 0,
  margen numeric(16, 2) NOT NULL DEFAULT 0,
  margen_pct numeric(8, 4),
  estado_pago text NOT NULL DEFAULT 'pendiente',
  motivo_cancelacion text,
  liquidacion_id uuid,
  creado_en timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE finanzas.liquidaciones (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  empleado_id uuid NOT NULL REFERENCES personal.empleados(id),
  periodo_desde date NOT NULL,
  periodo_hasta date NOT NULL,
  horas numeric(10, 2) NOT NULL DEFAULT 0,
  bonos numeric(16, 2) NOT NULL DEFAULT 0,
  propinas numeric(16, 2) NOT NULL DEFAULT 0,
  descuentos numeric(16, 2) NOT NULL DEFAULT 0,
  adelantos numeric(16, 2) NOT NULL DEFAULT 0,
  total numeric(16, 2) NOT NULL DEFAULT 0,
  estado text NOT NULL DEFAULT 'generada',
  comprobante_archivo_id uuid,
  pagada_en timestamptz,
  creado_en timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT liquidaciones_estado_chk CHECK (estado IN ('generada', 'pagada', 'anulada'))
);

ALTER TABLE finanzas.planilla_ingresos
  ADD CONSTRAINT planilla_liquidacion_fk
  FOREIGN KEY (liquidacion_id) REFERENCES finanzas.liquidaciones(id);

CREATE TABLE finanzas.adelantos (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  empleado_id uuid NOT NULL REFERENCES personal.empleados(id),
  monto numeric(16, 2) NOT NULL,
  comision numeric(16, 2) NOT NULL DEFAULT 0,
  neto numeric(16, 2) NOT NULL,
  estado text NOT NULL DEFAULT 'aprobado',
  liquidacion_id uuid REFERENCES finanzas.liquidaciones(id),
  creado_en timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT adelantos_estado_chk CHECK (estado IN ('aprobado', 'descontado', 'rechazado'))
);

CREATE TABLE finanzas.facturas (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  orden_id uuid REFERENCES operacion.ordenes(id),
  cliente_id uuid NOT NULL REFERENCES crm.clientes(id),
  numero text NOT NULL UNIQUE,
  tipo text NOT NULL DEFAULT 'factura',
  receptor_tipo text NOT NULL DEFAULT 'consumidor_final',
  receptor_doc text,
  receptor_nombre text,
  timbrado text,
  establecimiento text,
  punto_expedicion text,
  subtotal numeric(16, 2) NOT NULL,
  iva numeric(16, 2) NOT NULL,
  total numeric(16, 2) NOT NULL,
  estado text NOT NULL DEFAULT 'pendiente_sifen',
  cdc text,
  vencimiento date,
  payload jsonb NOT NULL DEFAULT '{}'::jsonb,
  creado_en timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT facturas_tipo_chk CHECK (tipo IN ('factura', 'nota_credito')),
  CONSTRAINT facturas_estado_chk CHECK (estado IN ('pendiente_sifen', 'emitida', 'anulada'))
);

CREATE SEQUENCE finanzas.factura_seq;

CREATE TABLE comercial.combos (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  nombre text NOT NULL,
  descripcion text NOT NULL DEFAULT '',
  imagen text,
  tipo_precio text NOT NULL,
  valor numeric(16, 4) NOT NULL,
  vigente_desde timestamptz,
  vigente_hasta timestamptz,
  cupo integer,
  usos integer NOT NULL DEFAULT 0,
  acumulable boolean NOT NULL DEFAULT false,
  publico_web boolean NOT NULL DEFAULT true,
  activo boolean NOT NULL DEFAULT true,
  creado_en timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT combos_tipo_precio_chk CHECK (tipo_precio IN ('fijo', 'porcentaje'))
);

CREATE TABLE comercial.combo_items (
  id smallserial PRIMARY KEY,
  combo_id uuid NOT NULL REFERENCES comercial.combos(id) ON DELETE CASCADE,
  tipo_limpieza_id smallint REFERENCES catalogo.tipos_limpieza(id),
  elemento_id smallint REFERENCES catalogo.elementos(id),
  cantidad integer NOT NULL DEFAULT 1
);

CREATE TABLE comercial.combo_zonas (
  combo_id uuid NOT NULL REFERENCES comercial.combos(id) ON DELETE CASCADE,
  zona_id smallint NOT NULL REFERENCES catalogo.zonas(id),
  PRIMARY KEY (combo_id, zona_id)
);

CREATE TABLE comercial.combo_hogares (
  combo_id uuid NOT NULL REFERENCES comercial.combos(id) ON DELETE CASCADE,
  tipo_hogar_id smallint NOT NULL REFERENCES catalogo.tipos_hogar(id),
  PRIMARY KEY (combo_id, tipo_hogar_id)
);

CREATE TABLE comercial.promociones (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  nombre text NOT NULL,
  tipo text NOT NULL,
  valor numeric(16, 4) NOT NULL,
  es_porcentaje boolean NOT NULL DEFAULT true,
  tope numeric(16, 2),
  minimo numeric(16, 2),
  segmento text NOT NULL DEFAULT 'todos',
  zona_id smallint REFERENCES catalogo.zonas(id),
  vigente_desde timestamptz,
  vigente_hasta timestamptz,
  cupo integer,
  usos integer NOT NULL DEFAULT 0,
  acumulable boolean NOT NULL DEFAULT false,
  prioridad integer NOT NULL DEFAULT 0,
  metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
  activo boolean NOT NULL DEFAULT true,
  creado_en timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT promociones_tipo_chk CHECK (tipo IN (
    'primera_compra', 'horario_valle', 'recurrencia', 'oferta_flash',
    'cumpleanos', 'empresas', 'campana_zona', 'reactivacion', 'cuarta_consecutiva'
  ))
);

CREATE TABLE comercial.cupones (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  codigo text NOT NULL UNIQUE,
  promocion_id uuid REFERENCES comercial.promociones(id),
  nombre text NOT NULL,
  es_porcentaje boolean NOT NULL DEFAULT true,
  valor numeric(16, 4) NOT NULL,
  tope numeric(16, 2),
  minimo numeric(16, 2),
  uso_maximo integer,
  uso_por_cliente integer NOT NULL DEFAULT 1,
  usos integer NOT NULL DEFAULT 0,
  vence_en timestamptz,
  acumulable boolean NOT NULL DEFAULT false,
  activo boolean NOT NULL DEFAULT true,
  creado_en timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE comercial.cupon_usos (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  cupon_id uuid NOT NULL REFERENCES comercial.cupones(id),
  cliente_id uuid NOT NULL REFERENCES crm.clientes(id),
  orden_id uuid REFERENCES operacion.ordenes(id),
  monto numeric(16, 2) NOT NULL,
  fecha timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE comercial.puntos_movimientos (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  cliente_id uuid NOT NULL REFERENCES crm.clientes(id),
  orden_id uuid REFERENCES operacion.ordenes(id),
  tipo text NOT NULL,
  puntos integer NOT NULL,
  detalle text,
  vence_en timestamptz,
  fecha timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT puntos_tipo_chk CHECK (tipo IN ('ganado', 'canjeado', 'vencido', 'ajuste'))
);

CREATE TABLE comercial.referidos (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  referidor_id uuid NOT NULL REFERENCES crm.clientes(id),
  referido_id uuid NOT NULL UNIQUE REFERENCES crm.clientes(id),
  estado text NOT NULL DEFAULT 'pendiente',
  credito_otorgado numeric(16, 2) NOT NULL DEFAULT 0,
  creado_en timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT referidos_estado_chk CHECK (estado IN ('pendiente', 'acreditado'))
);

ALTER TABLE operacion.ordenes
  ADD CONSTRAINT ordenes_combo_fk FOREIGN KEY (combo_id) REFERENCES comercial.combos(id);

ALTER TABLE operacion.ordenes
  ADD CONSTRAINT ordenes_plan_fk FOREIGN KEY (plan_id) REFERENCES operacion.planes_recurrentes(id);
