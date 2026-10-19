CREATE SEQUENCE operacion.orden_seq;

CREATE TABLE operacion.ordenes (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  numero text NOT NULL UNIQUE,
  cliente_id uuid NOT NULL REFERENCES crm.clientes(id),
  direccion_id uuid REFERENCES crm.direcciones(id),
  empleado_id uuid REFERENCES personal.empleados(id),
  empresa_id uuid REFERENCES crm.empresas(id),
  sucursal_id uuid REFERENCES crm.sucursales(id),
  centro_costo_id uuid REFERENCES crm.centros_costo(id),
  tipo_hogar_id smallint REFERENCES catalogo.tipos_hogar(id),
  tipo_limpieza_id smallint REFERENCES catalogo.tipos_limpieza(id),
  combo_id uuid,
  plan_id uuid,
  garantia_de_orden_id uuid REFERENCES operacion.ordenes(id),
  estado text NOT NULL DEFAULT 'BORRADOR',
  fecha date,
  hora_inicio time,
  horas_est numeric(8, 2) NOT NULL DEFAULT 0,
  horas_reales numeric(8, 2),
  horas_extra_aprobadas numeric(8, 2) NOT NULL DEFAULT 0,
  subtotal numeric(16, 2) NOT NULL DEFAULT 0,
  descuentos numeric(16, 2) NOT NULL DEFAULT 0,
  recargos numeric(16, 2) NOT NULL DEFAULT 0,
  traslado numeric(16, 2) NOT NULL DEFAULT 0,
  iva numeric(16, 2) NOT NULL DEFAULT 0,
  total numeric(16, 2) NOT NULL DEFAULT 0,
  anticipo numeric(16, 2) NOT NULL DEFAULT 0,
  anticipo_pagado numeric(16, 2) NOT NULL DEFAULT 0,
  saldo numeric(16, 2) NOT NULL DEFAULT 0,
  presupuesto_maximo numeric(16, 2),
  detalle_precio jsonb NOT NULL DEFAULT '{}'::jsonb,
  detalle_descuentos jsonb NOT NULL DEFAULT '[]'::jsonb,
  margen_calculado numeric(8, 4),
  costo_empleado numeric(16, 2) NOT NULL DEFAULT 0,
  es_emergencia boolean NOT NULL DEFAULT false,
  es_recurrente boolean NOT NULL DEFAULT false,
  es_garantia boolean NOT NULL DEFAULT false,
  preferencia_empleado_id uuid REFERENCES personal.empleados(id),
  notas text,
  factura_a_nombre text,
  cupon_codigo text,
  puntos_canje integer NOT NULL DEFAULT 0,
  origen text NOT NULL DEFAULT 'app',
  conformidad boolean,
  conformidad_en timestamptz,
  calificacion_promedio numeric(4, 2),
  enviada_en timestamptz,
  asignada_en timestamptz,
  confirmada_en timestamptz,
  en_camino_en timestamptz,
  checkin_en timestamptz,
  checkout_en timestamptz,
  completada_en timestamptz,
  pagada_en timestamptz,
  cancelada_en timestamptz,
  motivo_cancelacion text,
  cancelada_por text,
  creado_en timestamptz NOT NULL DEFAULT now(),
  actualizado_en timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT ordenes_estado_chk CHECK (estado IN (
    'BORRADOR', 'ENVIADA', 'EN_REVISION', 'ASIGNADA', 'CONFIRMADA',
    'EN_CAMINO', 'EN_CURSO', 'COMPLETADA', 'PAGADA', 'CALIFICADA',
    'CANCELADA', 'NO_SHOW', 'DISPUTA'
  ))
);

CREATE UNIQUE INDEX ordenes_un_borrador_idx ON operacion.ordenes (cliente_id) WHERE estado = 'BORRADOR';
CREATE INDEX ordenes_cliente_idx ON operacion.ordenes (cliente_id, creado_en DESC);
CREATE INDEX ordenes_empleado_idx ON operacion.ordenes (empleado_id, fecha);
CREATE INDEX ordenes_estado_idx ON operacion.ordenes (estado, es_emergencia, creado_en);
CREATE INDEX ordenes_fecha_idx ON operacion.ordenes (fecha);

CREATE TABLE operacion.orden_elementos (
  id smallserial PRIMARY KEY,
  orden_id uuid NOT NULL REFERENCES operacion.ordenes(id) ON DELETE CASCADE,
  elemento_id smallint NOT NULL REFERENCES catalogo.elementos(id),
  cantidad integer NOT NULL DEFAULT 1,
  precio numeric(16, 2) NOT NULL,
  minutos integer NOT NULL DEFAULT 0,
  UNIQUE (orden_id, elemento_id)
);

CREATE TABLE operacion.orden_tareas (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  orden_id uuid NOT NULL REFERENCES operacion.ordenes(id) ON DELETE CASCADE,
  tarea_catalogo_id smallint REFERENCES catalogo.tareas(id),
  ambiente text NOT NULL,
  nombre text NOT NULL,
  obligatoria boolean NOT NULL DEFAULT true,
  completada boolean NOT NULL DEFAULT false,
  hora timestamptz,
  nota text,
  orden smallint NOT NULL DEFAULT 0,
  extra boolean NOT NULL DEFAULT false
);

CREATE INDEX orden_tareas_orden_idx ON operacion.orden_tareas (orden_id);

CREATE TABLE operacion.orden_fotos (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  orden_id uuid NOT NULL REFERENCES operacion.ordenes(id) ON DELETE CASCADE,
  tipo text NOT NULL,
  archivo_id uuid NOT NULL,
  creado_por uuid REFERENCES seguridad.usuarios(id),
  creado_en timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT orden_fotos_tipo_chk CHECK (tipo IN ('antes', 'despues', 'incidente', 'fachada', 'checkin'))
);

CREATE TABLE operacion.orden_eventos (
  id bigserial PRIMARY KEY,
  orden_id uuid NOT NULL REFERENCES operacion.ordenes(id) ON DELETE CASCADE,
  estado text NOT NULL,
  actor_id uuid REFERENCES seguridad.usuarios(id),
  nota text,
  meta jsonb NOT NULL DEFAULT '{}'::jsonb,
  fecha timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX orden_eventos_orden_idx ON operacion.orden_eventos (orden_id, fecha);

CREATE TABLE operacion.registro_llegadas (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  orden_id uuid NOT NULL REFERENCES operacion.ordenes(id),
  empleado_id uuid NOT NULL REFERENCES personal.empleados(id),
  tipo text NOT NULL,
  hora timestamptz NOT NULL DEFAULT now(),
  lat double precision NOT NULL,
  lng double precision NOT NULL,
  distancia_m integer NOT NULL,
  manual boolean NOT NULL DEFAULT false,
  nota text,
  foto_archivo_id uuid,
  revisado boolean,
  revisado_por uuid REFERENCES seguridad.usuarios(id),
  CONSTRAINT llegadas_tipo_chk CHECK (tipo IN ('check-in', 'check-out'))
);

CREATE TABLE operacion.ubicaciones_empleado (
  id bigserial PRIMARY KEY,
  orden_id uuid REFERENCES operacion.ordenes(id),
  empleado_id uuid NOT NULL REFERENCES personal.empleados(id),
  lat double precision NOT NULL,
  lng double precision NOT NULL,
  fecha_hora timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX ubicaciones_orden_idx ON operacion.ubicaciones_empleado (orden_id, fecha_hora DESC);
CREATE INDEX ubicaciones_empleado_idx ON operacion.ubicaciones_empleado (empleado_id, fecha_hora DESC);

CREATE TABLE operacion.ofertas (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  orden_id uuid NOT NULL REFERENCES operacion.ordenes(id),
  empleado_id uuid NOT NULL REFERENCES personal.empleados(id),
  estado text NOT NULL DEFAULT 'pendiente',
  puntaje numeric(8, 4),
  detalle_puntaje jsonb NOT NULL DEFAULT '{}'::jsonb,
  pago_estimado numeric(16, 2) NOT NULL DEFAULT 0,
  distancia_m integer,
  zona_nombre text,
  expira_en timestamptz NOT NULL,
  motivo_rechazo text,
  respondida_en timestamptz,
  creado_en timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT ofertas_estado_chk CHECK (estado IN ('pendiente', 'aceptada', 'rechazada', 'expirada', 'cancelada'))
);

CREATE INDEX ofertas_empleado_idx ON operacion.ofertas (empleado_id, estado, expira_en);

CREATE TABLE operacion.imprevistos (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  orden_id uuid NOT NULL REFERENCES operacion.ordenes(id),
  empleado_id uuid REFERENCES personal.empleados(id),
  tipo text NOT NULL,
  detalle text,
  minutos_extra integer,
  estado text NOT NULL DEFAULT 'abierto',
  creado_en timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT imprevistos_tipo_chk CHECK (tipo IN (
    'necesito_mas_tiempo', 'sin_acceso', 'falta_insumo', 'dano', 'cliente_ausente', 'otro'
  ))
);

CREATE TABLE operacion.extras_tiempo (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  orden_id uuid NOT NULL REFERENCES operacion.ordenes(id),
  minutos integer NOT NULL,
  motivo text NOT NULL,
  monto numeric(16, 2) NOT NULL DEFAULT 0,
  estado text NOT NULL DEFAULT 'pendiente',
  respondido_en timestamptz,
  creado_en timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT extras_estado_chk CHECK (estado IN ('pendiente', 'aprobado', 'rechazado'))
);

CREATE TABLE operacion.mensajes (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  orden_id uuid NOT NULL REFERENCES operacion.ordenes(id) ON DELETE CASCADE,
  autor_id uuid NOT NULL REFERENCES seguridad.usuarios(id),
  autor_rol text NOT NULL,
  texto text NOT NULL,
  creado_en timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX mensajes_orden_idx ON operacion.mensajes (orden_id, creado_en);

CREATE TABLE operacion.calificaciones (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  orden_id uuid NOT NULL REFERENCES operacion.ordenes(id),
  de_usuario_id uuid NOT NULL REFERENCES seguridad.usuarios(id),
  de_rol text NOT NULL,
  a_usuario_id uuid REFERENCES seguridad.usuarios(id),
  puntaje numeric(4, 2) NOT NULL,
  categorias jsonb NOT NULL DEFAULT '{}'::jsonb,
  comentario text,
  propina numeric(16, 2) NOT NULL DEFAULT 0,
  creado_en timestamptz NOT NULL DEFAULT now(),
  UNIQUE (orden_id, de_rol)
);

CREATE TABLE operacion.planes_recurrentes (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  cliente_id uuid NOT NULL REFERENCES crm.clientes(id),
  direccion_id uuid REFERENCES crm.direcciones(id),
  tipo_hogar_id smallint REFERENCES catalogo.tipos_hogar(id),
  tipo_limpieza_id smallint REFERENCES catalogo.tipos_limpieza(id),
  elementos jsonb NOT NULL DEFAULT '[]'::jsonb,
  frecuencia text NOT NULL,
  dia_semana smallint,
  hora_inicio time,
  empleado_fijo_id uuid REFERENCES personal.empleados(id),
  estado text NOT NULL DEFAULT 'activo',
  proxima_fecha date,
  creado_en timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT planes_frecuencia_chk CHECK (frecuencia IN ('semanal', 'quincenal')),
  CONSTRAINT planes_estado_chk CHECK (estado IN ('activo', 'pausado', 'cancelado'))
);

CREATE TABLE operacion.incidentes (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  orden_id uuid REFERENCES operacion.ordenes(id),
  reportado_por uuid REFERENCES seguridad.usuarios(id),
  tipo text NOT NULL,
  descripcion text NOT NULL,
  estado text NOT NULL DEFAULT 'abierto',
  resolucion text,
  monto numeric(16, 2),
  resuelto_por uuid REFERENCES seguridad.usuarios(id),
  creado_en timestamptz NOT NULL DEFAULT now(),
  resuelto_en timestamptz,
  CONSTRAINT incidentes_estado_chk CHECK (estado IN ('abierto', 'en_revision', 'resuelto', 'rechazado'))
);

CREATE TABLE operacion.enlaces_seguimiento (
  token text PRIMARY KEY,
  orden_id uuid NOT NULL REFERENCES operacion.ordenes(id) ON DELETE CASCADE,
  expira_en timestamptz NOT NULL,
  creado_en timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE operacion.cambios_turno (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  orden_id uuid NOT NULL REFERENCES operacion.ordenes(id),
  empleado_origen_id uuid NOT NULL REFERENCES personal.empleados(id),
  empleado_destino_id uuid REFERENCES personal.empleados(id),
  estado text NOT NULL DEFAULT 'pendiente_destino',
  creado_en timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT cambios_estado_chk CHECK (estado IN (
    'pendiente_destino', 'pendiente_admin', 'aprobado', 'rechazado'
  ))
);

CREATE TABLE operacion.sos (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  empleado_id uuid NOT NULL REFERENCES personal.empleados(id),
  orden_id uuid REFERENCES operacion.ordenes(id),
  lat double precision,
  lng double precision,
  creado_en timestamptz NOT NULL DEFAULT now(),
  atendido boolean NOT NULL DEFAULT false
);

CREATE TABLE operacion.tickets_soporte (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  usuario_id uuid NOT NULL REFERENCES seguridad.usuarios(id),
  orden_id uuid REFERENCES operacion.ordenes(id),
  asunto text NOT NULL,
  estado text NOT NULL DEFAULT 'abierto',
  creado_en timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT tickets_estado_chk CHECK (estado IN ('abierto', 'respondido', 'cerrado'))
);

CREATE TABLE operacion.ticket_mensajes (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  ticket_id uuid NOT NULL REFERENCES operacion.tickets_soporte(id) ON DELETE CASCADE,
  autor_id uuid NOT NULL REFERENCES seguridad.usuarios(id),
  texto text NOT NULL,
  creado_en timestamptz NOT NULL DEFAULT now()
);
