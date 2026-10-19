-- Esquemas y seguridad. Sin ORM: el esquema es la fuente de verdad.
CREATE SCHEMA IF NOT EXISTS seguridad;
CREATE SCHEMA IF NOT EXISTS catalogo;
CREATE SCHEMA IF NOT EXISTS crm;
CREATE SCHEMA IF NOT EXISTS personal;
CREATE SCHEMA IF NOT EXISTS operacion;
CREATE SCHEMA IF NOT EXISTS finanzas;
CREATE SCHEMA IF NOT EXISTS comercial;
CREATE SCHEMA IF NOT EXISTS contenido;
CREATE SCHEMA IF NOT EXISTS sistema;

CREATE TABLE IF NOT EXISTS sistema.schema_migrations (
  archivo text PRIMARY KEY,
  aplicado_en timestamptz NOT NULL DEFAULT now()
);

CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- Punto en polígono GeoJSON (anillo exterior, coordenadas [lng, lat]).
-- PostGIS no está instalado en este servidor local; la regla vive en la base.
CREATE OR REPLACE FUNCTION catalogo.punto_en_poligono(
  p_lat double precision,
  p_lng double precision,
  p_poly jsonb
) RETURNS boolean
LANGUAGE plpgsql
IMMUTABLE
AS $$
DECLARE
  ring jsonb;
  n int;
  i int;
  j int;
  xi double precision;
  yi double precision;
  xj double precision;
  yj double precision;
  inside boolean := false;
BEGIN
  IF p_poly IS NULL OR jsonb_typeof(p_poly) <> 'array' THEN
    RETURN false;
  END IF;
  ring := p_poly -> 0;
  IF ring IS NULL THEN
    RETURN false;
  END IF;
  n := jsonb_array_length(ring);
  IF n < 4 THEN
    RETURN false;
  END IF;
  j := n - 1;
  FOR i IN 0..n - 1 LOOP
    xi := (ring -> i ->> 0)::double precision;
    yi := (ring -> i ->> 1)::double precision;
    xj := (ring -> j ->> 0)::double precision;
    yj := (ring -> j ->> 1)::double precision;
    IF ((yi > p_lat) <> (yj > p_lat))
       AND (p_lng < (xj - xi) * (p_lat - yi) / NULLIF(yj - yi, 0) + xi) THEN
      inside := NOT inside;
    END IF;
    j := i;
  END LOOP;
  RETURN inside;
END;
$$;

CREATE OR REPLACE FUNCTION catalogo.distancia_metros(
  lat1 double precision,
  lng1 double precision,
  lat2 double precision,
  lng2 double precision
) RETURNS double precision
LANGUAGE sql
IMMUTABLE
AS $$
  SELECT 6371000 * 2 * asin(least(1, sqrt(
    power(sin(radians(lat2 - lat1) / 2), 2)
    + cos(radians(lat1)) * cos(radians(lat2)) * power(sin(radians(lng2 - lng1) / 2), 2)
  )));
$$;

CREATE TABLE seguridad.roles (
  id smallserial PRIMARY KEY,
  codigo text NOT NULL UNIQUE,
  nombre text NOT NULL
);

CREATE TABLE seguridad.permisos (
  id smallserial PRIMARY KEY,
  codigo text NOT NULL UNIQUE,
  descripcion text NOT NULL
);

CREATE TABLE seguridad.rol_permisos (
  rol_id smallint NOT NULL REFERENCES seguridad.roles(id),
  permiso_id smallint NOT NULL REFERENCES seguridad.permisos(id),
  PRIMARY KEY (rol_id, permiso_id)
);

CREATE TABLE seguridad.usuarios (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  rol_id smallint NOT NULL REFERENCES seguridad.roles(id),
  cedula text NOT NULL UNIQUE,
  password_hash text NOT NULL,
  debe_cambiar_password boolean NOT NULL DEFAULT false,
  password_expira_en timestamptz,
  intentos_fallidos integer NOT NULL DEFAULT 0,
  bloqueado_hasta timestamptz,
  estado text NOT NULL DEFAULT 'activo',
  nombre text NOT NULL,
  apellido text NOT NULL DEFAULT '',
  telefono text,
  correo text,
  idioma text NOT NULL DEFAULT 'es',
  telefono_verificado boolean NOT NULL DEFAULT false,
  ultimo_login timestamptz,
  creado_en timestamptz NOT NULL DEFAULT now(),
  actualizado_en timestamptz NOT NULL DEFAULT now(),
  creado_por uuid REFERENCES seguridad.usuarios(id),
  CONSTRAINT usuarios_estado_chk CHECK (estado IN ('activo', 'bloqueado', 'suspendido', 'pendiente_verificacion')),
  CONSTRAINT usuarios_idioma_chk CHECK (idioma IN ('es', 'gn', 'en')),
  CONSTRAINT usuarios_cedula_chk CHECK (cedula ~ '^[0-9]{5,10}$')
);

CREATE INDEX usuarios_estado_idx ON seguridad.usuarios (estado);

CREATE TABLE seguridad.usuario_permisos (
  usuario_id uuid NOT NULL REFERENCES seguridad.usuarios(id) ON DELETE CASCADE,
  permiso_id smallint NOT NULL REFERENCES seguridad.permisos(id),
  concedido boolean NOT NULL,
  PRIMARY KEY (usuario_id, permiso_id)
);

CREATE TABLE seguridad.refresh_tokens (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  usuario_id uuid NOT NULL REFERENCES seguridad.usuarios(id) ON DELETE CASCADE,
  token_hash text NOT NULL UNIQUE,
  familia uuid NOT NULL,
  expira_en timestamptz NOT NULL,
  revocado boolean NOT NULL DEFAULT false,
  reemplazado_por uuid,
  user_agent text,
  ip inet,
  dispositivo text,
  creado_en timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX refresh_usuario_idx ON seguridad.refresh_tokens (usuario_id, revocado);

CREATE TABLE seguridad.auditoria_acceso (
  id bigserial PRIMARY KEY,
  usuario_id uuid REFERENCES seguridad.usuarios(id),
  cedula text,
  exito boolean NOT NULL,
  motivo text,
  ip inet,
  user_agent text,
  dispositivo text,
  fecha timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX auditoria_acceso_fecha_idx ON seguridad.auditoria_acceso (fecha DESC);

CREATE TABLE seguridad.otp_codigos (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  usuario_id uuid REFERENCES seguridad.usuarios(id) ON DELETE CASCADE,
  destino text NOT NULL,
  proposito text NOT NULL,
  codigo_hash text NOT NULL,
  expira_en timestamptz NOT NULL,
  usado boolean NOT NULL DEFAULT false,
  intentos integer NOT NULL DEFAULT 0,
  canal text NOT NULL DEFAULT 'whatsapp',
  metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
  creado_en timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX otp_destino_idx ON seguridad.otp_codigos (destino, proposito, usado);

CREATE TABLE seguridad.registros_pendientes (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  cedula text NOT NULL,
  nombre text NOT NULL,
  apellido text NOT NULL DEFAULT '',
  telefono text NOT NULL,
  correo text,
  password_hash text NOT NULL,
  acepta_terminos boolean NOT NULL,
  codigo_referido text,
  borrador jsonb,
  codigo_hash text NOT NULL,
  expira_en timestamptz NOT NULL,
  intentos integer NOT NULL DEFAULT 0,
  canal text NOT NULL DEFAULT 'whatsapp',
  creado_en timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX registros_pendientes_cedula_idx ON seguridad.registros_pendientes (cedula, creado_en DESC);

CREATE TABLE seguridad.rate_limits (
  clave text PRIMARY KEY,
  ventana_inicio timestamptz NOT NULL,
  contador integer NOT NULL,
  actualizado_en timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE seguridad.webauthn_credenciales (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  usuario_id uuid NOT NULL REFERENCES seguridad.usuarios(id) ON DELETE CASCADE,
  credential_id text NOT NULL UNIQUE,
  public_key bytea NOT NULL,
  sign_count bigint NOT NULL DEFAULT 0,
  dispositivo text,
  creado_en timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE seguridad.webauthn_retos (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  usuario_id uuid REFERENCES seguridad.usuarios(id) ON DELETE CASCADE,
  cedula text,
  reto text NOT NULL,
  proposito text NOT NULL,
  expira_en timestamptz NOT NULL,
  creado_en timestamptz NOT NULL DEFAULT now()
);
