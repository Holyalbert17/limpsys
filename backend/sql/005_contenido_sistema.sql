CREATE TABLE contenido.contenido_sitio (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  clave text NOT NULL,
  idioma text NOT NULL DEFAULT 'es',
  valor jsonb NOT NULL,
  vigente_desde timestamptz,
  vigente_hasta timestamptz,
  actualizado_en timestamptz NOT NULL DEFAULT now(),
  UNIQUE (clave, idioma),
  CONSTRAINT contenido_idioma_chk CHECK (idioma IN ('es', 'gn', 'en'))
);

CREATE TABLE contenido.faq (
  id smallserial PRIMARY KEY,
  pregunta text NOT NULL,
  respuesta text NOT NULL,
  orden smallint NOT NULL DEFAULT 0,
  idioma text NOT NULL DEFAULT 'es',
  activo boolean NOT NULL DEFAULT true
);

CREATE TABLE contenido.testimonios (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  nombre text NOT NULL,
  texto text NOT NULL,
  puntaje numeric(4, 2) NOT NULL DEFAULT 5,
  foto_url text,
  zona text,
  curado boolean NOT NULL DEFAULT true,
  activo boolean NOT NULL DEFAULT true,
  creado_en timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE contenido.banners (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  titulo text NOT NULL,
  subtitulo text,
  imagen text,
  enlace text,
  vigente_desde timestamptz,
  vigente_hasta timestamptz,
  orden smallint NOT NULL DEFAULT 0,
  activo boolean NOT NULL DEFAULT true
);

CREATE TABLE contenido.postulaciones (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  nombre text NOT NULL,
  apellido text NOT NULL DEFAULT '',
  cedula text NOT NULL,
  telefono text NOT NULL,
  correo text,
  zonas text,
  mensaje text,
  documentos jsonb NOT NULL DEFAULT '[]'::jsonb,
  estado text NOT NULL DEFAULT 'recibida',
  creado_en timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT postulaciones_estado_chk CHECK (estado IN ('recibida', 'en_revision', 'aceptada', 'rechazada'))
);

CREATE TABLE contenido.contactos (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  nombre text NOT NULL,
  telefono text,
  correo text,
  mensaje text NOT NULL,
  origen text NOT NULL DEFAULT 'contacto',
  creado_en timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE contenido.cotizaciones_empresa (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  empresa text NOT NULL,
  ruc text,
  contacto text NOT NULL,
  telefono text NOT NULL,
  correo text,
  sucursales integer,
  horas_mes numeric(10, 2),
  mensaje text,
  estado text NOT NULL DEFAULT 'nueva',
  creado_en timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE sistema.parametros (
  clave text PRIMARY KEY,
  valor jsonb NOT NULL,
  grupo text NOT NULL,
  descripcion text,
  modificado_por uuid REFERENCES seguridad.usuarios(id),
  actualizado_en timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE sistema.parametros_historial (
  id bigserial PRIMARY KEY,
  clave text NOT NULL,
  valor_anterior jsonb,
  valor_nuevo jsonb,
  usuario_id uuid REFERENCES seguridad.usuarios(id),
  fecha timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX parametros_historial_clave_idx ON sistema.parametros_historial (clave, fecha DESC);

CREATE TABLE sistema.parametros_programados (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  clave text NOT NULL,
  valor jsonb NOT NULL,
  vigente_desde timestamptz NOT NULL,
  aplicado boolean NOT NULL DEFAULT false,
  creado_por uuid REFERENCES seguridad.usuarios(id),
  creado_en timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE sistema.notificaciones (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  usuario_id uuid REFERENCES seguridad.usuarios(id),
  evento text NOT NULL,
  canal text NOT NULL,
  titulo text NOT NULL,
  cuerpo text NOT NULL,
  estado text NOT NULL DEFAULT 'pendiente',
  data jsonb NOT NULL DEFAULT '{}'::jsonb,
  leida boolean NOT NULL DEFAULT false,
  fecha timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT notificaciones_canal_chk CHECK (canal IN ('push', 'whatsapp', 'sms', 'correo', 'panel'))
);

CREATE INDEX notificaciones_usuario_idx ON sistema.notificaciones (usuario_id, fecha DESC);

CREATE TABLE sistema.plantillas_notificacion (
  evento text PRIMARY KEY,
  titulo text NOT NULL,
  cuerpo text NOT NULL,
  canales text[] NOT NULL
);

CREATE TABLE sistema.preferencias_notificacion (
  usuario_id uuid PRIMARY KEY REFERENCES seguridad.usuarios(id) ON DELETE CASCADE,
  push boolean NOT NULL DEFAULT true,
  whatsapp boolean NOT NULL DEFAULT true,
  sms boolean NOT NULL DEFAULT false,
  correo boolean NOT NULL DEFAULT true
);

CREATE TABLE sistema.push_suscripciones (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  usuario_id uuid NOT NULL REFERENCES seguridad.usuarios(id) ON DELETE CASCADE,
  endpoint text NOT NULL UNIQUE,
  p256dh text,
  auth text,
  user_agent text,
  creado_en timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE sistema.auditoria (
  id bigserial PRIMARY KEY,
  usuario_id uuid REFERENCES seguridad.usuarios(id),
  accion text NOT NULL,
  entidad text NOT NULL,
  entidad_id text,
  antes jsonb,
  despues jsonb,
  ip inet,
  fecha timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX auditoria_fecha_idx ON sistema.auditoria (fecha DESC);
CREATE INDEX auditoria_entidad_idx ON sistema.auditoria (entidad, entidad_id);

CREATE TABLE sistema.idempotencia (
  clave text PRIMARY KEY,
  usuario_id uuid,
  ruta text NOT NULL,
  body_hash text NOT NULL,
  respuesta jsonb,
  estado_http integer,
  creado_en timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE sistema.archivos (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  clave text NOT NULL UNIQUE,
  nombre text,
  content_type text,
  tamano integer,
  carpeta text NOT NULL,
  dueno_id uuid REFERENCES seguridad.usuarios(id),
  metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
  creado_en timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE sistema.cache_geo (
  clave text PRIMARY KEY,
  valor jsonb NOT NULL,
  expira_en timestamptz NOT NULL
);

CREATE TABLE sistema.tareas_control (
  nombre text PRIMARY KEY,
  ultima_vez timestamptz
);

CREATE TABLE sistema.eventos_conversion (
  id bigserial PRIMARY KEY,
  nombre text NOT NULL,
  usuario_id uuid,
  metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
  fecha timestamptz NOT NULL DEFAULT now()
);
