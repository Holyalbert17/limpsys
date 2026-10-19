INSERT INTO seguridad.roles (codigo, nombre) VALUES
  ('superadmin', 'Super administrador'),
  ('admin', 'Administrador'),
  ('cliente', 'Cliente'),
  ('empleado', 'Empleado');

INSERT INTO seguridad.permisos (codigo, descripcion) VALUES
  ('ordenes.ver', 'Ver la cola y el detalle de solicitudes'),
  ('ordenes.asignar', 'Asignar un empleado a una solicitud'),
  ('ordenes.reasignar', 'Reasignar una solicitud con motivo'),
  ('ordenes.cancelar', 'Cancelar solicitudes desde el panel'),
  ('finanzas.ver', 'Ver planilla, margen y cobros'),
  ('finanzas.liquidar', 'Generar liquidaciones de empleados'),
  ('finanzas.conciliar', 'Conciliar pagos'),
  ('parametros.ver', 'Ver parámetros'),
  ('parametros.editar', 'Editar parámetros operativos'),
  ('parametros.criticos', 'Editar parámetros críticos'),
  ('clientes.ver', 'Ver clientes'),
  ('clientes.crear', 'Dar de alta clientes'),
  ('clientes.editar', 'Editar clientes'),
  ('clientes.bloquear', 'Bloquear o reactivar clientes'),
  ('empleados.ver', 'Ver empleados'),
  ('empleados.crear', 'Dar de alta empleados'),
  ('empleados.editar', 'Editar empleados'),
  ('empleados.sancionar', 'Sancionar o suspender empleados'),
  ('promociones.editar', 'Gestionar combos, ofertas y cupones'),
  ('contenido.editar', 'Editar el sitio público'),
  ('reportes.ver', 'Ver reportes y KPIs'),
  ('reportes.exportar', 'Exportar reportes'),
  ('auditoria.ver', 'Ver auditoría'),
  ('usuarios.admin', 'Administrar usuarios del panel'),
  ('incidentes.resolver', 'Resolver incidentes y disputas'),
  ('facturacion.emitir', 'Marcar comprobantes emitidos'),
  ('mapa.ver', 'Ver el mapa en vivo');

INSERT INTO seguridad.rol_permisos (rol_id, permiso_id)
SELECT r.id, p.id
FROM seguridad.roles r
JOIN seguridad.permisos p ON true
WHERE r.codigo = 'superadmin';

INSERT INTO seguridad.rol_permisos (rol_id, permiso_id)
SELECT r.id, p.id
FROM seguridad.roles r
JOIN seguridad.permisos p ON p.codigo NOT IN ('usuarios.admin', 'parametros.criticos')
WHERE r.codigo = 'admin';

INSERT INTO catalogo.tipos_limpieza (codigo, nombre, descripcion, incluye, tarifa_hora, factor_horas, foto_antes_obligatoria, orden) VALUES
  ('regular', 'Regular', 'Mantenimiento semanal del hogar.', 'Pisos, polvo, baños, cocina de uso diario y cambio de ropa de cama.', 45000, 1.0, false, 1),
  ('profunda', 'Profunda', 'Limpieza detallada de toda la vivienda.', 'Todo lo regular más interiores, zócalos, artefactos y ventanas interiores.', 70000, 1.6, true, 2),
  ('post_obra', 'Post-obra', 'Retiro de polvo de obra y limpieza final.', 'Polvo fino, pisos, vidrios, artefactos y desinfección.', 90000, 2.2, true, 3),
  ('oficina', 'Empresa / oficina', 'Limpieza de espacios de trabajo.', 'Puestos, baños, cocina de oficina y áreas comunes.', 60000, 1.2, false, 4);

INSERT INTO catalogo.tipos_hogar (codigo, nombre, descripcion, horas_base, m2_orientativo, orden) VALUES
  ('depto_chico', 'Departamento chico', 'Hasta un dormitorio.', 2, '40–70 m²', 1),
  ('casa_mediana', 'Casa mediana', 'Dos o tres dormitorios.', 4, '80–150 m²', 2),
  ('casa_grande', 'Casa grande', 'Cuatro dormitorios o más.', 6, '150 m² o más', 3);

INSERT INTO catalogo.elementos (codigo, nombre, descripcion, adicional, minutos, orden) VALUES
  ('heladera', 'Heladera', 'Interior de la heladera.', 25000, 30, 1),
  ('horno', 'Horno', 'Interior del horno.', 20000, 25, 2),
  ('ventanas', 'Ventanas', 'Vidrios y marcos.', 30000, 40, 3),
  ('balcon', 'Balcón', 'Balcón o terraza.', 15000, 20, 4),
  ('tapizados', 'Tapizados', 'Aspirado de sillones y sillas.', 35000, 45, 5),
  ('pisos', 'Pisos especiales', 'Tratamiento de pisos.', 20000, 30, 6),
  ('desinfeccion', 'Desinfección', 'Desinfección de superficies.', 18000, 20, 7);

INSERT INTO catalogo.zonas (codigo, nombre, poligono, traslado) VALUES
  (
    'centro',
    'Asunción centro',
    '[[[-57.67,-25.30],[-57.60,-25.30],[-57.60,-25.26],[-57.67,-25.26],[-57.67,-25.30]]]'::jsonb,
    15000
  ),
  (
    'gran_asuncion',
    'Gran Asunción',
    '[[[-57.75,-25.40],[-57.45,-25.40],[-57.45,-25.15],[-57.75,-25.15],[-57.75,-25.40]]]'::jsonb,
    25000
  );

INSERT INTO catalogo.tareas (tipo_limpieza_id, ambiente, nombre, orden, obligatoria)
SELECT t.id, v.ambiente, v.nombre, v.orden, true
FROM catalogo.tipos_limpieza t
JOIN (VALUES
  ('regular', 'Cocina', 'Mesadas, pileta y artefactos exteriores', 1),
  ('regular', 'Baños', 'Sanitarios, duchas y pisos', 2),
  ('regular', 'Dormitorios', 'Camas, polvo y pisos', 3),
  ('regular', 'Living', 'Superficies y pisos', 4),
  ('profunda', 'Cocina', 'Interiores accesibles y artefactos', 1),
  ('profunda', 'Baños', 'Sarro, juntas y desinfección', 2),
  ('profunda', 'Dormitorios', 'Interior de muebles accesibles', 3),
  ('profunda', 'Living', 'Zócalos, puertas y ventanas interiores', 4),
  ('profunda', 'Exteriores', 'Balcón o ingreso', 5),
  ('post_obra', 'Obra', 'Retiro de polvo fino', 1),
  ('post_obra', 'Pisos', 'Lavado de pisos', 2),
  ('post_obra', 'Vidrios', 'Vidrios y marcos', 3),
  ('post_obra', 'Baños', 'Desinfección final', 4),
  ('oficina', 'Puestos', 'Escritorios y equipos por fuera', 1),
  ('oficina', 'Baños', 'Sanitarios y reposición visible', 2),
  ('oficina', 'Cocina', 'Kitchenette', 3),
  ('oficina', 'Comunes', 'Pasillos y recepción', 4)
) AS v(codigo, ambiente, nombre, orden) ON v.codigo = t.codigo;

INSERT INTO catalogo.feriados (fecha, nombre) VALUES
  ('2026-01-01', 'Año Nuevo'),
  ('2026-03-01', 'Día de los Héroes'),
  ('2026-04-02', 'Jueves Santo'),
  ('2026-04-03', 'Viernes Santo'),
  ('2026-05-01', 'Día del Trabajador'),
  ('2026-05-14', 'Independencia Nacional'),
  ('2026-05-15', 'Independencia Nacional'),
  ('2026-06-12', 'Paz del Chaco'),
  ('2026-08-15', 'Fundación de Asunción'),
  ('2026-09-29', 'Victoria de Boquerón'),
  ('2026-12-08', 'Virgen de Caacupé'),
  ('2026-12-25', 'Navidad');

INSERT INTO catalogo.franjas (nombre, tipo, dias, hora_desde, hora_hasta, valor) VALUES
  ('Valle mañana', 'valle', ARRAY[1,2,3,4,5], '09:00', '11:00', 10),
  ('Pico tarde', 'pico', ARRAY[1,2,3,4,5], '17:00', '20:00', 1.15),
  ('Nocturno', 'nocturno', ARRAY[1,2,3,4,5,6,7], '20:00', '06:00', 1.25);

INSERT INTO finanzas.medios_pago (codigo, nombre, requiere_confirmacion) VALUES
  ('tarjeta', 'Tarjeta', true),
  ('transferencia', 'Transferencia', true),
  ('billetera', 'Billetera', true),
  ('efectivo', 'Efectivo', true),
  ('saldo', 'Saldo a favor', false);

INSERT INTO comercial.combos (nombre, descripcion, tipo_precio, valor, acumulable, publico_web) VALUES
  ('Hogar Express', 'Limpieza regular con heladera y horno.', 'porcentaje', 10, false, true),
  ('Renovación', 'Limpieza profunda con ventanas y tapizados.', 'porcentaje', 12, false, true),
  ('Mudanza', 'Profunda con heladera, horno y balcón.', 'porcentaje', 15, false, true),
  ('Post-obra Total', 'Post-obra con ventanas, pisos y desinfección.', 'porcentaje', 12, false, true);

INSERT INTO comercial.combo_items (combo_id, tipo_limpieza_id, elemento_id)
SELECT c.id, t.id, NULL
FROM comercial.combos c
JOIN catalogo.tipos_limpieza t ON (
  (c.nombre = 'Hogar Express' AND t.codigo = 'regular')
  OR (c.nombre IN ('Renovación', 'Mudanza') AND t.codigo = 'profunda')
  OR (c.nombre = 'Post-obra Total' AND t.codigo = 'post_obra')
);

INSERT INTO comercial.combo_items (combo_id, tipo_limpieza_id, elemento_id)
SELECT c.id, NULL, e.id
FROM comercial.combos c
JOIN catalogo.elementos e ON (
  (c.nombre = 'Hogar Express' AND e.codigo IN ('heladera', 'horno'))
  OR (c.nombre = 'Renovación' AND e.codigo IN ('ventanas', 'tapizados'))
  OR (c.nombre = 'Mudanza' AND e.codigo IN ('heladera', 'horno', 'balcon'))
  OR (c.nombre = 'Post-obra Total' AND e.codigo IN ('ventanas', 'pisos', 'desinfeccion'))
);

INSERT INTO comercial.promociones (nombre, tipo, valor, es_porcentaje, tope, segmento, acumulable, prioridad) VALUES
  ('Primera limpieza', 'primera_compra', 15, true, 50000, 'nuevos', false, 10),
  ('Cuarta consecutiva', 'cuarta_consecutiva', 5, true, NULL, 'recurrentes', true, 5);

INSERT INTO comercial.cupones (codigo, nombre, es_porcentaje, valor, tope, uso_por_cliente, acumulable) VALUES
  ('LIMSYS10', 'Cupón de bienvenida', true, 10, 40000, 1, false);

INSERT INTO sistema.parametros (clave, valor, grupo, descripcion) VALUES
  ('minimo_horas', '2', 'precios', 'Horas mínimas por orden, además del traslado.'),
  ('pago_empleado_hora', '20000', 'empleados', 'Pago por hora trabajada. Ejemplo del documento: 25.000; este valor deja margen sobre el piso.'),
  ('cargas_porcentaje', '0.10', 'empleados', 'Cargas sobre el costo de mano de obra.'),
  ('insumos_por_hora', '2000', 'precios', 'Costo de insumos por hora estimada.'),
  ('recargo_nocturno', '1.25', 'recargos', 'Multiplicador nocturno.'),
  ('recargo_fin_semana', '1.20', 'recargos', 'Multiplicador de sábado y domingo.'),
  ('recargo_feriado', '1.35', 'recargos', 'Multiplicador de feriado.'),
  ('recargo_emergencia', '1.40', 'recargos', 'Multiplicador de emergencia.'),
  ('recargo_preferencia', '1.08', 'recargos', 'Multiplicador si el cliente elige empleado.'),
  ('descuento_plan_recurrente', '0.10', 'descuentos', 'Descuento de planes semanales o quincenales.'),
  ('tope_descuento_orden', '0.30', 'descuentos', 'Tope de descuento sobre el subtotal.'),
  ('tope_descuento_cliente_mes', '500000', 'descuentos', 'Tope de descuento por cliente en el mes, en guaraníes.'),
  ('margen_minimo', '0.35', 'descuentos', 'Piso de margen. Por debajo se recorta el descuento.'),
  ('margen_piso_absoluto', '0.20', 'descuentos', 'Piso absoluto para campañas aprobadas.'),
  ('anticipo_porcentaje', '0.30', 'pagos', 'Anticipo por defecto.'),
  ('plazo_empresa_dias', '15', 'pagos', 'Plazo de crédito para empresas.'),
  ('tiempo_aceptacion_min', '30', 'operacion', 'Minutos para aceptar una oferta.'),
  ('radio_checkin_m', '150', 'operacion', 'Radio de check-in y check-out, en metros.'),
  ('alerta_demora_min', '15', 'operacion', 'Minutos de demora antes de alertar.'),
  ('horario_atencion_desde', '"07:00"', 'operacion', 'Apertura de la agenda.'),
  ('horario_atencion_hasta', '"19:00"', 'operacion', 'Cierre de la agenda.'),
  ('slot_minutos', '30', 'operacion', 'Duración de cada franja ofrecida.'),
  ('buffer_traslado_min', '30', 'operacion', 'Minutos de separación entre servicios.'),
  ('asignacion_automatica', 'false', 'asignacion', 'Si es verdadero, la cola se asigna sola.'),
  ('peso_cercania', '0.40', 'asignacion', 'Peso de cercanía.'),
  ('peso_calificacion', '0.30', 'asignacion', 'Peso de calificación.'),
  ('peso_disponibilidad', '0.20', 'asignacion', 'Peso de disponibilidad.'),
  ('peso_carga', '0.10', 'asignacion', 'Peso de equilibrio de carga.'),
  ('distancia_max_m', '20000', 'asignacion', 'Distancia de referencia para el puntaje de cercanía.'),
  ('retencion_ubicacion_dias', '90', 'operacion', 'Días que se guarda el trayecto.'),
  ('umbral_rechazo', '0.30', 'empleados', 'Rechazo por encima de este porcentaje baja la prioridad.'),
  ('cancelaciones_suspension_mes', '3', 'empleados', 'Cancelaciones del empleado en el mes que suspenden.'),
  ('bono_calificacion', '5000', 'empleados', 'Bono si la calificación alcanza el umbral.'),
  ('umbral_calificacion_bono', '4.7', 'empleados', 'Nota mínima para el bono de calidad.'),
  ('bono_puntualidad', '3000', 'empleados', 'Bono por check-in a tiempo.'),
  ('adelanto_tope_porcentaje', '0.50', 'empleados', 'Tope del adelanto sobre lo ya ganado.'),
  ('adelanto_comision', '0.05', 'empleados', 'Comisión del adelanto.'),
  ('calificacion_inicial', '4.5', 'empleados', 'Calificación hasta tener servicios propios.'),
  ('nivel_plata_servicios', '20', 'empleados', 'Servicios para nivel plata.'),
  ('nivel_oro_servicios', '50', 'empleados', 'Servicios para nivel oro.'),
  ('nivel_plata_nota', '4.5', 'empleados', 'Nota mínima para plata.'),
  ('nivel_oro_nota', '4.7', 'empleados', 'Nota mínima para oro.'),
  ('intentos_maximos', '5', 'login', 'Intentos fallidos antes del bloqueo.'),
  ('bloqueo_minutos', '15', 'login', 'Minutos de bloqueo.'),
  ('captcha_tras_fallos', '3', 'login', 'Fallos antes de pedir captcha.'),
  ('password_inicial_horas', '72', 'login', 'Caducidad de la contraseña inicial.'),
  ('autoregistro', 'true', 'login', 'Permite el registro público.'),
  ('login_whatsapp', 'false', 'login', 'Permite ingresar con código.'),
  ('verificar_telefono_primer_ingreso', 'false', 'login', 'Pide verificar el teléfono al entrar.'),
  ('plazo_calificacion_dias', '7', 'operacion', 'Días para calificar antes del cierre automático.'),
  ('iva_divisor', '11', 'facturacion', 'IVA 10% incluido: total / 11.'),
  ('timbrado', '"00000000"', 'facturacion', 'Timbrado SIFEN. Completar con el timbrado real.'),
  ('establecimiento', '"001"', 'facturacion', 'Establecimiento.'),
  ('punto_expedicion', '"001"', 'facturacion', 'Punto de expedición.'),
  ('razon_social_emisor', '"Limsys"', 'facturacion', 'Razón social del emisor.'),
  ('ruc_emisor', '""', 'facturacion', 'RUC del emisor.'),
  ('garantia_horas', '24', 'garantia', 'Horas para pedir la re-limpieza.'),
  ('garantia_nota_max', '3', 'garantia', 'Nota máxima que habilita la garantía.'),
  ('garantia_tope_cliente', '2', 'garantia', 'Garantías gratis por cliente.'),
  ('puntos_cada_gs', '10000', 'puntos', 'Guaraníes para ganar un punto.'),
  ('puntos_valor_gs', '100', 'puntos', 'Guaraníes de descuento por punto.'),
  ('referido_credito', '20000', 'puntos', 'Crédito para quien invita y para el invitado.'),
  ('puntos_vencen_dias', '365', 'puntos', 'Vencimiento de los puntos.'),
  ('factor_ruta', '1.3', 'mapas', 'Factor sobre la distancia en línea recta si no hay Distance Matrix.'),
  ('velocidad_urbana_kmh', '30', 'mapas', 'Velocidad usada para el ETA estimado.'),
  ('cancelacion', '[
    {"nombre":"mas_24h","horas_min":24,"cargo":0,"compensacion":0},
    {"nombre":"entre_4_y_24","horas_min":4,"cargo":0.30,"compensacion":0.50},
    {"nombre":"menos_4h","horas_min":0,"cargo":1,"compensacion":0,"hora_minima_empleado":1}
  ]'::jsonb, 'cancelacion', 'Ventanas de cancelación.'),
  ('numero_enmascarado', '""', 'operacion', 'Número de la central de llamadas enmascaradas.'),
  ('whatsapp_origen', '""', 'notificaciones', 'Remitente de WhatsApp cuando haya proveedor.');

INSERT INTO sistema.plantillas_notificacion (evento, titulo, cuerpo, canales) VALUES
  ('bienvenida', 'Tu acceso a Limsys está listo', 'Hola {{nombre}}. Ya podés ingresar con tu número de cédula. En el primer ingreso te vamos a pedir una contraseña propia.', ARRAY['whatsapp','sms']),
  ('solicitud_enviada', 'Recibimos tu solicitud', 'Tu solicitud {{numero}} entró en la cola. Te avisamos cuando tenga empleado.', ARRAY['push','whatsapp','panel']),
  ('asignacion', 'Tenés una oferta nueva', 'Hay un servicio {{tipo}} el {{fecha}} a las {{hora}}. Tenés {{minutos}} minutos para responder.', ARRAY['push','panel']),
  ('confirmacion', 'Tu limpieza está confirmada', '{{empleado}} confirmó el servicio {{numero}}.', ARRAY['push']),
  ('en_camino', 'Tu empleado va en camino', '{{empleado}} salió hacia tu dirección. Llegada estimada: {{eta}}.', ARRAY['push','whatsapp']),
  ('demora', 'El servicio viene con demora', 'El servicio {{numero}} supera en {{minutos}} minutos la hora pactada.', ARRAY['push','panel']),
  ('checkin', 'Empezó la limpieza', '{{empleado}} ya está en el lugar.', ARRAY['push']),
  ('checkout', 'La limpieza terminó', 'El servicio {{numero}} quedó completado. Podés calificarlo.', ARRAY['push','correo']),
  ('pago', 'Recibimos tu pago', 'Registramos un pago de Gs. {{monto}} por {{numero}}.', ARRAY['correo','whatsapp']),
  ('cancelacion', 'Se canceló un servicio', 'El servicio {{numero}} fue cancelado. Motivo: {{motivo}}.', ARRAY['push','panel']),
  ('otp', 'Tu código de Limsys', 'Tu código es {{codigo}}. Vence en 10 minutos.', ARRAY['whatsapp','sms']),
  ('sos', 'Alerta SOS', '{{empleado}} pidió ayuda. Orden: {{numero}}.', ARRAY['panel','push']);

INSERT INTO contenido.contenido_sitio (clave, idioma, valor) VALUES
  ('tema', 'es', '{"logo_url": null, "color_primario": "#0F766E", "color_secundario": "#134E4A", "color_acento": "#F59E0B", "texto_bienvenida": "Limpieza a domicilio, sin complicaciones", "subtitulo": "Pedís en minutos, seguís en vivo y pagás un precio claro.", "imagen_fondo": null, "idioma_defecto": "es"}'::jsonb),
  ('hero', 'es', '{"titulo": "Tu casa limpia, con gente verificada", "subtitulo": "Elegí el servicio, mirá el precio y seguí al empleado en el mapa.", "boton_principal": "Solicitar limpieza", "boton_secundario": "Registrarme gratis"}'::jsonb),
  ('como_funciona', 'es', '[{"titulo":"Elegí","texto":"Tipo de hogar, limpieza y extras. El precio se actualiza al instante."},{"titulo":"Agendá","texto":"Solo ves horarios con gente disponible."},{"titulo":"Seguí en vivo","texto":"Sabés cuándo sale y cuándo llega."},{"titulo":"Calificá","texto":"Si algo no quedó bien, la garantía cubre una nueva visita."}]'::jsonb),
  ('confianza', 'es', '[{"titulo":"Empleados verificados","texto":"Identidad, antecedentes y capacitación."},{"titulo":"Garantía de re-limpieza","texto":"Si la nota es baja, volvemos sin costo."},{"titulo":"Seguimiento en vivo","texto":"Como un viaje en una app de transporte."},{"titulo":"Pago seguro","texto":"Anticipo o total, con factura."}]'::jsonb),
  ('nosotros', 'es', '{"historia": "Limsys organiza limpiezas a domicilio y para empresas en Gran Asunción.", "mision": "Que pedir una limpieza sea tan claro como pedir un viaje.", "valores": ["Precio cerrado", "Puntualidad", "Respeto por el hogar"]}'::jsonb),
  ('contacto', 'es', '{"telefono": "+595000000000", "correo": "hola@limsys.local", "whatsapp": "+595000000000", "direccion": "Asunción, Paraguay"}'::jsonb),
  ('terminos', 'es', '{"titulo": "Términos y condiciones", "cuerpo": "Al crear una cuenta aceptás que la cédula identifica tu usuario y que los precios salen de los parámetros vigentes al confirmar la solicitud."}'::jsonb),
  ('privacidad', 'es', '{"titulo": "Privacidad", "cuerpo": "La ubicación del empleado se comparte solo durante el camino y el servicio, y se borra según el plazo configurado. La dirección exacta se muestra al empleado recién cuando confirma."}'::jsonb),
  ('cancelacion_texto', 'es', '{"titulo": "Cancelaciones", "cuerpo": "Con más de 24 horas de anticipación el reembolso es total. Entre 24 y 4 horas hay un cargo. Con menos de 4 horas o si nadie recibe al empleado, el cargo es total."}'::jsonb);

INSERT INTO contenido.faq (pregunta, respuesta, orden) VALUES
  ('¿Cómo pago?', 'Podés dejar un anticipo o pagar el total con transferencia, tarjeta, billetera, efectivo o saldo a favor. El resto se cobra al terminar.', 1),
  ('¿Puedo cancelar?', 'Sí. El cargo depende de cuánto falta para el horario y está publicado en la política de cancelación. Los porcentajes se configuran en el panel.', 2),
  ('¿Por qué la contraseña inicial es la cédula?', 'Solo cuando un administrador crea la cuenta. Caduca a las pocas horas y el primer ingreso obliga a cambiarla. Si te registrás vos, elegís tu clave desde el principio.', 3),
  ('¿En qué zonas trabajan?', 'En las zonas dibujadas en el mapa. Si tu pin queda afuera, podés anotarte en la lista de espera.', 4),
  ('¿Qué pasa si no me gusta el resultado?', 'Con una calificación baja podés pedir una re-limpieza gratuita dentro del plazo de garantía.', 5);

INSERT INTO contenido.testimonios (nombre, texto, puntaje, zona) VALUES
  ('Laura', 'Pedí a la mañana y a la tarde la casa ya estaba lista. El precio del presupuesto fue el que pagué.', 5, 'Asunción centro'),
  ('Miguel', 'Seguí al empleado en el mapa y llegó en el horario. Los baños quedaron muy bien.', 5, 'Gran Asunción');

INSERT INTO personal.capacitaciones (nombre, descripcion, obligatoria, tipo_limpieza_id)
SELECT 'Inducción general', 'Uniforme, trato, seguridad y uso de la app.', true, NULL
UNION ALL
SELECT 'Post-obra', 'Polvo fino, químicos y orden de las áreas.', true, id FROM catalogo.tipos_limpieza WHERE codigo = 'post_obra';

INSERT INTO personal.insumos (nombre, stock, unidad) VALUES
  ('Paños de microfibra', 100, 'un'),
  ('Desinfectante', 40, 'l'),
  ('Bolsas', 200, 'un');
