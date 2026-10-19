CREATE TABLE catalogo.tipos_limpieza (
  id smallserial PRIMARY KEY,
  codigo text NOT NULL UNIQUE,
  nombre text NOT NULL,
  descripcion text NOT NULL DEFAULT '',
  incluye text NOT NULL DEFAULT '',
  tarifa_hora numeric(16, 2) NOT NULL,
  factor_horas numeric(8, 3) NOT NULL,
  foto_antes_obligatoria boolean NOT NULL DEFAULT false,
  activo boolean NOT NULL DEFAULT true,
  orden smallint NOT NULL DEFAULT 0
);

CREATE TABLE catalogo.tipos_hogar (
  id smallserial PRIMARY KEY,
  codigo text NOT NULL UNIQUE,
  nombre text NOT NULL,
  descripcion text NOT NULL DEFAULT '',
  horas_base numeric(8, 2) NOT NULL,
  m2_orientativo text,
  activo boolean NOT NULL DEFAULT true,
  orden smallint NOT NULL DEFAULT 0
);

CREATE TABLE catalogo.elementos (
  id smallserial PRIMARY KEY,
  codigo text NOT NULL UNIQUE,
  nombre text NOT NULL,
  descripcion text NOT NULL DEFAULT '',
  adicional numeric(16, 2) NOT NULL DEFAULT 0,
  minutos integer NOT NULL DEFAULT 0,
  activo boolean NOT NULL DEFAULT true,
  orden smallint NOT NULL DEFAULT 0
);

CREATE TABLE catalogo.zonas (
  id smallserial PRIMARY KEY,
  codigo text NOT NULL UNIQUE,
  nombre text NOT NULL,
  poligono jsonb NOT NULL,
  traslado numeric(16, 2) NOT NULL DEFAULT 0,
  activo boolean NOT NULL DEFAULT true
);

CREATE TABLE catalogo.tareas (
  id smallserial PRIMARY KEY,
  tipo_limpieza_id smallint NOT NULL REFERENCES catalogo.tipos_limpieza(id),
  ambiente text NOT NULL,
  nombre text NOT NULL,
  orden smallint NOT NULL DEFAULT 0,
  obligatoria boolean NOT NULL DEFAULT true,
  activo boolean NOT NULL DEFAULT true
);

CREATE TABLE catalogo.feriados (
  fecha date PRIMARY KEY,
  nombre text NOT NULL,
  recargo numeric(8, 4)
);

CREATE TABLE catalogo.franjas (
  id smallserial PRIMARY KEY,
  nombre text NOT NULL,
  tipo text NOT NULL,
  dias smallint[] NOT NULL,
  hora_desde time NOT NULL,
  hora_hasta time NOT NULL,
  valor numeric(8, 4) NOT NULL,
  activo boolean NOT NULL DEFAULT true,
  CONSTRAINT franjas_tipo_chk CHECK (tipo IN ('valle', 'pico', 'nocturno'))
);

CREATE TABLE crm.clientes (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  usuario_id uuid NOT NULL UNIQUE REFERENCES seguridad.usuarios(id),
  tipo text NOT NULL DEFAULT 'persona',
  nombre text NOT NULL,
  apellido text NOT NULL DEFAULT '',
  telefono text,
  correo text,
  ruc text,
  razon_social text,
  fecha_nacimiento date,
  puntos_saldo integer NOT NULL DEFAULT 0,
  credito_saldo numeric(16, 2) NOT NULL DEFAULT 0,
  bloqueado boolean NOT NULL DEFAULT false,
  notas text,
  codigo_referido text NOT NULL UNIQUE,
  referido_por uuid REFERENCES crm.clientes(id),
  tour_pendiente boolean NOT NULL DEFAULT true,
  creado_en timestamptz NOT NULL DEFAULT now(),
  actualizado_en timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT clientes_tipo_chk CHECK (tipo IN ('persona', 'empresa'))
);

CREATE TABLE crm.direcciones (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  cliente_id uuid NOT NULL REFERENCES crm.clientes(id),
  apodo text NOT NULL DEFAULT 'Casa',
  direccion text NOT NULL,
  lat double precision NOT NULL,
  lng double precision NOT NULL,
  place_id text,
  referencia text,
  piso text,
  instrucciones text,
  foto_fachada_id uuid,
  zona_id smallint REFERENCES catalogo.zonas(id),
  es_principal boolean NOT NULL DEFAULT false,
  activo boolean NOT NULL DEFAULT true,
  creado_en timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX direcciones_cliente_idx ON crm.direcciones (cliente_id) WHERE activo;

CREATE TABLE crm.empresas (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  cliente_id uuid NOT NULL UNIQUE REFERENCES crm.clientes(id),
  razon_social text NOT NULL,
  ruc text,
  tarifa_hora_preferencial numeric(16, 2),
  compromiso_horas_mes numeric(10, 2),
  plazo_dias integer NOT NULL DEFAULT 15,
  activo boolean NOT NULL DEFAULT true,
  creado_en timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE crm.sucursales (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  empresa_id uuid NOT NULL REFERENCES crm.empresas(id),
  nombre text NOT NULL,
  direccion_id uuid REFERENCES crm.direcciones(id),
  activo boolean NOT NULL DEFAULT true
);

CREATE TABLE crm.centros_costo (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  empresa_id uuid NOT NULL REFERENCES crm.empresas(id),
  codigo text NOT NULL,
  nombre text NOT NULL,
  activo boolean NOT NULL DEFAULT true,
  UNIQUE (empresa_id, codigo)
);

CREATE TABLE crm.miembros_empresa (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  empresa_id uuid NOT NULL REFERENCES crm.empresas(id),
  usuario_id uuid NOT NULL REFERENCES seguridad.usuarios(id),
  rol_cuenta text NOT NULL DEFAULT 'solicitante',
  creado_en timestamptz NOT NULL DEFAULT now(),
  UNIQUE (empresa_id, usuario_id),
  CONSTRAINT miembros_rol_chk CHECK (rol_cuenta IN ('admin_cuenta', 'solicitante', 'lector'))
);

CREATE TABLE crm.lista_espera (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  cliente_id uuid REFERENCES crm.clientes(id),
  nombre text,
  telefono text,
  direccion text,
  lat double precision,
  lng double precision,
  creado_en timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE personal.empleados (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  usuario_id uuid NOT NULL UNIQUE REFERENCES seguridad.usuarios(id),
  calificacion numeric(4, 2) NOT NULL DEFAULT 0,
  servicios_completados integer NOT NULL DEFAULT 0,
  nivel text NOT NULL DEFAULT 'bronce',
  estado text NOT NULL DEFAULT 'activo',
  disponible boolean NOT NULL DEFAULT false,
  en_guardia boolean NOT NULL DEFAULT false,
  tarifa_hora numeric(16, 2),
  medio_cobro jsonb NOT NULL DEFAULT '{}'::jsonb,
  foto_archivo_id uuid,
  contacto_emergencia_nombre text,
  contacto_emergencia_telefono text,
  distancia_max_km numeric(8, 2),
  tipos_preferidos smallint[] NOT NULL DEFAULT '{}',
  creado_en timestamptz NOT NULL DEFAULT now(),
  actualizado_en timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT empleados_nivel_chk CHECK (nivel IN ('bronce', 'plata', 'oro')),
  CONSTRAINT empleados_estado_chk CHECK (estado IN ('activo', 'suspendido', 'inactivo'))
);

CREATE TABLE personal.disponibilidad (
  id smallserial PRIMARY KEY,
  empleado_id uuid NOT NULL REFERENCES personal.empleados(id) ON DELETE CASCADE,
  dia_semana smallint NOT NULL,
  hora_desde time NOT NULL,
  hora_hasta time NOT NULL,
  CONSTRAINT disponibilidad_dia_chk CHECK (dia_semana BETWEEN 1 AND 7),
  UNIQUE (empleado_id, dia_semana, hora_desde)
);

CREATE TABLE personal.bloqueos (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  empleado_id uuid NOT NULL REFERENCES personal.empleados(id) ON DELETE CASCADE,
  desde timestamptz NOT NULL,
  hasta timestamptz NOT NULL,
  motivo text,
  creado_en timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE personal.empleado_zonas (
  empleado_id uuid NOT NULL REFERENCES personal.empleados(id) ON DELETE CASCADE,
  zona_id smallint NOT NULL REFERENCES catalogo.zonas(id),
  PRIMARY KEY (empleado_id, zona_id)
);

CREATE TABLE personal.documentos (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  empleado_id uuid NOT NULL REFERENCES personal.empleados(id) ON DELETE CASCADE,
  tipo text NOT NULL,
  archivo_id uuid NOT NULL,
  estado text NOT NULL DEFAULT 'pendiente',
  nota text,
  vence_en date,
  creado_en timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT documentos_estado_chk CHECK (estado IN ('pendiente', 'aprobado', 'rechazado'))
);

CREATE TABLE personal.capacitaciones (
  id smallserial PRIMARY KEY,
  nombre text NOT NULL,
  descripcion text NOT NULL DEFAULT '',
  video_url text,
  obligatoria boolean NOT NULL DEFAULT false,
  tipo_limpieza_id smallint REFERENCES catalogo.tipos_limpieza(id),
  activo boolean NOT NULL DEFAULT true
);

CREATE TABLE personal.empleado_capacitacion (
  empleado_id uuid NOT NULL REFERENCES personal.empleados(id) ON DELETE CASCADE,
  capacitacion_id smallint NOT NULL REFERENCES personal.capacitaciones(id),
  completada boolean NOT NULL DEFAULT false,
  fecha timestamptz,
  PRIMARY KEY (empleado_id, capacitacion_id)
);

CREATE TABLE personal.sanciones (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  empleado_id uuid NOT NULL REFERENCES personal.empleados(id),
  tipo text NOT NULL,
  motivo text NOT NULL,
  puntos integer NOT NULL DEFAULT 0,
  creado_por uuid REFERENCES seguridad.usuarios(id),
  creado_en timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE personal.ausencias (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  empleado_id uuid NOT NULL REFERENCES personal.empleados(id),
  desde date NOT NULL,
  hasta date NOT NULL,
  motivo text NOT NULL,
  estado text NOT NULL DEFAULT 'pendiente',
  resuelto_por uuid REFERENCES seguridad.usuarios(id),
  creado_en timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT ausencias_estado_chk CHECK (estado IN ('pendiente', 'aprobada', 'rechazada'))
);

CREATE TABLE personal.insumos (
  id smallserial PRIMARY KEY,
  nombre text NOT NULL,
  stock numeric(12, 2) NOT NULL DEFAULT 0,
  unidad text NOT NULL DEFAULT 'un',
  activo boolean NOT NULL DEFAULT true
);

CREATE TABLE personal.solicitudes_insumo (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  empleado_id uuid NOT NULL REFERENCES personal.empleados(id),
  insumo_id smallint NOT NULL REFERENCES personal.insumos(id),
  cantidad numeric(12, 2) NOT NULL,
  estado text NOT NULL DEFAULT 'pendiente',
  creado_en timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT solicitudes_insumo_estado_chk CHECK (estado IN ('pendiente', 'entregada', 'rechazada'))
);
