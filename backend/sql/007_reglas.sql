-- Calendario, reserva, equipo, cobro y datos que el recorrido del servicio todavía no guardaba.

ALTER TABLE operacion.ordenes
  ADD COLUMN IF NOT EXISTS cantidad_empleados smallint NOT NULL DEFAULT 1,
  ADD COLUMN IF NOT EXISTS insumos_modo text NOT NULL DEFAULT 'empresa',
  ADD COLUMN IF NOT EXISTS pin text,
  ADD COLUMN IF NOT EXISTS reprogramaciones smallint NOT NULL DEFAULT 0;

ALTER TABLE operacion.ordenes DROP CONSTRAINT IF EXISTS ordenes_insumos_chk;
ALTER TABLE operacion.ordenes
  ADD CONSTRAINT ordenes_insumos_chk CHECK (insumos_modo IN ('cliente', 'empresa'));

ALTER TABLE operacion.ordenes DROP CONSTRAINT IF EXISTS ordenes_equipo_chk;
ALTER TABLE operacion.ordenes
  ADD CONSTRAINT ordenes_equipo_chk CHECK (cantidad_empleados BETWEEN 1 AND 4);

CREATE TABLE IF NOT EXISTS operacion.orden_empleados (
  orden_id uuid NOT NULL REFERENCES operacion.ordenes(id) ON DELETE CASCADE,
  empleado_id uuid NOT NULL REFERENCES personal.empleados(id),
  rol text NOT NULL DEFAULT 'principal',
  PRIMARY KEY (orden_id, empleado_id)
);

ALTER TABLE crm.clientes
  ADD COLUMN IF NOT EXISTS preferencias_hogar jsonb NOT NULL DEFAULT '{}'::jsonb,
  ADD COLUMN IF NOT EXISTS consentimiento_version text,
  ADD COLUMN IF NOT EXISTS consentimiento_en timestamptz;

ALTER TABLE personal.empleados
  ADD COLUMN IF NOT EXISTS modalidad text NOT NULL DEFAULT 'independiente',
  ADD COLUMN IF NOT EXISTS pausa_hasta timestamptz,
  ADD COLUMN IF NOT EXISTS sucursal_operacion_id uuid;

ALTER TABLE personal.empleados DROP CONSTRAINT IF EXISTS empleados_modalidad_chk;
ALTER TABLE personal.empleados
  ADD CONSTRAINT empleados_modalidad_chk CHECK (modalidad IN ('independiente', 'dependiente'));

CREATE TABLE IF NOT EXISTS personal.medios_cobro (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  empleado_id uuid NOT NULL REFERENCES personal.empleados(id) ON DELETE CASCADE,
  tipo text NOT NULL,
  referencia text NOT NULL,
  verificado_en timestamptz,
  utilizable_desde timestamptz,
  activo boolean NOT NULL DEFAULT true,
  creado_en timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS finanzas.liquidacion_items (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  liquidacion_id uuid NOT NULL REFERENCES finanzas.liquidaciones(id) ON DELETE CASCADE,
  tipo text NOT NULL,
  orden_id uuid,
  monto numeric(16, 2) NOT NULL,
  descripcion text NOT NULL,
  disputado boolean NOT NULL DEFAULT false
);

CREATE TABLE IF NOT EXISTS finanzas.disputas_pago (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  item_id uuid NOT NULL REFERENCES finanzas.liquidacion_items(id),
  motivo text NOT NULL,
  estado text NOT NULL DEFAULT 'abierta',
  resolucion text,
  creado_en timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT disputas_pago_estado_chk CHECK (estado IN ('abierta', 'aceptada', 'rechazada'))
);

ALTER TABLE finanzas.liquidaciones DROP CONSTRAINT IF EXISTS liquidaciones_estado_chk;
ALTER TABLE finanzas.liquidaciones
  ADD CONSTRAINT liquidaciones_estado_chk CHECK (
    estado IN ('generada', 'en_revision', 'aprobada', 'pagada', 'conciliada', 'disputada', 'anulada')
  );

CREATE TABLE IF NOT EXISTS finanzas.cobros_plan (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  plan_id uuid NOT NULL REFERENCES operacion.planes_recurrentes(id),
  orden_id uuid REFERENCES operacion.ordenes(id),
  monto numeric(16, 2) NOT NULL DEFAULT 0,
  intento smallint NOT NULL DEFAULT 1,
  estado text NOT NULL DEFAULT 'pendiente',
  proximo_intento timestamptz NOT NULL DEFAULT now(),
  creado_en timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT cobros_plan_estado_chk CHECK (estado IN ('pendiente', 'confirmado', 'fallido'))
);

CREATE TABLE IF NOT EXISTS sistema.cola_fiscal (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  factura_id uuid NOT NULL REFERENCES finanzas.facturas(id),
  tipo text NOT NULL,
  estado text NOT NULL DEFAULT 'pendiente',
  intentos smallint NOT NULL DEFAULT 0,
  proximo_intento timestamptz NOT NULL DEFAULT now(),
  ultimo_error text,
  creado_en timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT cola_fiscal_estado_chk CHECK (estado IN ('pendiente', 'enviado', 'error'))
);

CREATE TABLE IF NOT EXISTS sistema.solicitudes_privacidad (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  usuario_id uuid NOT NULL REFERENCES seguridad.usuarios(id),
  tipo text NOT NULL,
  estado text NOT NULL DEFAULT 'pendiente',
  creado_en timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT privacidad_tipo_chk CHECK (tipo IN ('exportar', 'eliminar'))
);

CREATE TABLE IF NOT EXISTS catalogo.paises (
  codigo text PRIMARY KEY,
  nombre text NOT NULL,
  activo boolean NOT NULL DEFAULT true
);

CREATE TABLE IF NOT EXISTS catalogo.tipos_documento (
  id smallserial PRIMARY KEY,
  pais text NOT NULL REFERENCES catalogo.paises(codigo),
  codigo text NOT NULL,
  nombre text NOT NULL,
  UNIQUE (pais, codigo)
);

CREATE TABLE IF NOT EXISTS catalogo.monedas (
  codigo text PRIMARY KEY,
  nombre text NOT NULL,
  decimales smallint NOT NULL DEFAULT 0,
  es_base boolean NOT NULL DEFAULT false,
  habilitada boolean NOT NULL DEFAULT true
);

CREATE TABLE IF NOT EXISTS catalogo.tipos_cambio (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  moneda text NOT NULL REFERENCES catalogo.monedas(codigo),
  tasa_gs numeric(16, 4) NOT NULL,
  vigente_desde timestamptz NOT NULL DEFAULT now(),
  vigente_hasta timestamptz
);

CREATE TABLE IF NOT EXISTS catalogo.sucursales_operacion (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  nombre text NOT NULL,
  ciudad text NOT NULL,
  activa boolean NOT NULL DEFAULT true,
  creado_en timestamptz NOT NULL DEFAULT now()
);

ALTER TABLE seguridad.usuarios
  ADD COLUMN IF NOT EXISTS sucursal_operacion_id uuid REFERENCES catalogo.sucursales_operacion(id);

ALTER TABLE personal.empleados
  DROP CONSTRAINT IF EXISTS empleados_sucursal_fk;
ALTER TABLE personal.empleados
  ADD CONSTRAINT empleados_sucursal_fk
  FOREIGN KEY (sucursal_operacion_id) REFERENCES catalogo.sucursales_operacion(id);

INSERT INTO catalogo.paises (codigo, nombre) VALUES
  ('PY', 'Paraguay'),
  ('AR', 'Argentina'),
  ('BR', 'Brasil')
ON CONFLICT (codigo) DO NOTHING;

INSERT INTO catalogo.tipos_documento (pais, codigo, nombre) VALUES
  ('PY', 'CI', 'Cédula de identidad'),
  ('AR', 'DNI', 'Documento nacional de identidad'),
  ('BR', 'CPF', 'Cadastro de pessoas físicas')
ON CONFLICT (pais, codigo) DO NOTHING;

INSERT INTO catalogo.monedas (codigo, nombre, decimales, es_base, habilitada) VALUES
  ('PYG', 'Guaraní', 0, true, true),
  ('USD', 'Dólar', 2, false, false),
  ('ARS', 'Peso argentino', 2, false, false),
  ('BRL', 'Real', 2, false, false)
ON CONFLICT (codigo) DO NOTHING;

INSERT INTO sistema.parametros (clave, valor, grupo, descripcion) VALUES
  ('anticipacion_min_horas', '3', 'operacion', 'Horas mínimas para reservar. Las emergencias no aplican.'),
  ('anticipacion_max_dias', '90', 'operacion', 'Días máximos de anticipación para reservar.'),
  ('reprogramacion_horas', '6', 'operacion', 'Horas antes del turno en las que todavía se puede cambiar la fecha.'),
  ('reprogramacion_gratis', '1', 'operacion', 'Cambios de fecha sin cargo.'),
  ('visibilidad_antes_horas', '2', 'operacion', 'Horas antes del turno en las que el empleado ve la dirección y el teléfono.'),
  ('visibilidad_despues_horas', '2', 'operacion', 'Horas después del turno en las que el empleado sigue viendo esos datos.'),
  ('pin_checkin', 'true', 'operacion', 'Exige el PIN de 4 dígitos para iniciar el servicio.'),
  ('horas_max_dia', '10', 'empleados', 'Tope de horas de servicio por día.'),
  ('horas_max_semana', '48', 'empleados', 'Tope de horas de servicio por semana.'),
  ('medio_cobro_espera_horas', '24', 'empleados', 'Horas de espera después de cargar un medio de cobro.'),
  ('documento_aviso_dias', '30', 'empleados', 'Aviso cuando un documento vence dentro de estos días.'),
  ('cobro_plan_reintentos', '3', 'finanzas', 'Intentos de cobro de un plan antes de pausarlo.'),
  ('disputa_pago_horas', '48', 'finanzas', 'Horas para disputar un ítem de la liquidación.')
ON CONFLICT (clave) DO NOTHING;

UPDATE sistema.parametros SET valor = '"19:00"'::jsonb, descripcion = 'Cierre de la agenda.' WHERE clave = 'horario_atencion_hasta';
UPDATE sistema.parametros SET valor = '30'::jsonb, descripcion = 'Duración de cada franja ofrecida.' WHERE clave = 'slot_minutos';

INSERT INTO sistema.plantillas_notificacion (evento, titulo, cuerpo, canales) VALUES
  ('documento_por_vencer', 'Documento por vencer', 'Un documento vence el {vence_en}.', ARRAY['panel']),
  ('reprogramada', 'Servicio reprogramado', 'La solicitud cambió al {fecha} a las {hora}.', ARRAY['panel'])
ON CONFLICT (evento) DO NOTHING;
