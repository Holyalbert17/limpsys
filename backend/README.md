# Limsys API

Backend de la plataforma de limpieza para la web y la app móvil. FastAPI habla con PostgreSQL usando SQL directo: no hay ORM. El prefijo de la API es `/api/v1`. La documentación interactiva está en `/docs`. La raíz redirige ahí.

La base local de desarrollo es `limsys`, usuario `postgres`, contraseña `1`, puerto `5432`.

## Cómo levantarlo

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Si el puerto 8000 está ocupado, levantalo en otro. En el entorno local de desarrollo se usa `http://127.0.0.1:8001`, y los scripts de carga apuntan a ese puerto.

Al arrancar, el proceso aplica los SQL de `sql/` que todavía no figuran en `sistema.schema_migrations` y crea el superadmin si no existe. La cédula y la contraseña salen de `ADMIN_CEDULA` y `ADMIN_PASSWORD`. No se insertan clientes ni empleados en el arranque.

La salud responde en `GET /api/v1/salud`.

## Datos de prueba

El catálogo, los roles, los parámetros, las plantillas, los medios de pago y el contenido público viven en `sql/006_seed.sql`. No hay endpoints para dar de alta tipos de limpieza, zonas o feriados: eso es configuración inicial y después se edita donde la API lo permite (parámetros, combos, promociones, cupones y textos del sitio).

Las personas y la jornada se cargan por la API, con el mismo recorrido que va a usar el producto:

```powershell
.\.venv\Scripts\python.exe scripts\cargar_flujo.py
```

El script espera el servidor en `http://127.0.0.1:8001`. Hace esto:

1. El superadmin da de alta a Ana. La contraseña inicial es la cédula. Ella entra y la cambia.
2. Luis se registra en el sitio público, confirma el código y carga su dirección.
3. El superadmin da de alta a Rosa. Ella cambia la contraseña, carga horario de lunes a sábado, completa las capacitaciones obligatorias, se marca disponible y sube la cédula.
4. Entran un contacto, una cotización de empresa, una postulación y una dirección fuera de cobertura en la lista de espera.
5. Luis pide una limpieza regular de casa mediana, paga el total por transferencia y Rosa la lleva hasta calificada.
6. Ana deja otra solicitud confirmada para el día siguiente, para ver una agenda con un servicio próximo.

| Rol | Cédula | Contraseña | Cómo entró |
| --- | --- | --- | --- |
| Superadmin | `80000001` | `Admin#2026` | Variable de entorno, solo en el primer arranque |
| Cliente | `40000001` | `Cliente#2026` | Alta del panel. La clave inicial fue la cédula |
| Cliente | `40000002` | `Cliente#2026` | Autoregistro con código |
| Empleada | `50000001` | `Empleado#2026` | Alta del panel. La clave inicial fue la cédula |

En desarrollo el código de registro o de recuperación vuelve en `debug_codigo`. El captcha de desarrollo acepta `dev-captcha`. El webhook de pagos espera la cabecera `X-Webhook-Secret: dev-webhook`.

Si la cédula ya existe, el alta responde 409 y el script sigue con esa cuenta. Volver a correrlo intenta crear de nuevo las órdenes del recorrido y puede chocar con un horario ya ocupado.

Para dejar datos en el resto de los endpoints (cancelación, disputa, garantía, cambio de turno, liquidación, cupón, reportes y WebSocket):

```powershell
.\.venv\Scripts\python.exe scripts\probar_endpoints.py
```

Ese recorrido suma estas cuentas, todas con contraseña ya cambiada:

| Rol | Cédula | Contraseña |
| --- | --- | --- |
| Empleado | `50000003` | `Empleado#2026` |
| Cliente | `40000003` | `Cliente#2026` |
| Admin | `80000002` | `Admin#2026` |

Quedan solicitudes en borrador, confirmada, calificada, cancelada por el cliente, devuelta a cola por el empleado, en disputa resuelta y una re-limpieza por garantía. El cupón de prueba es `PRUEBA10`. Huella y Face ID exigen un dispositivo real: la API genera el reto y rechaza una credencial vacía.

## Esquemas

Cada consulta nombra el esquema. Son nueve:

| Esquema | Qué guarda |
| --- | --- |
| `seguridad` | Usuarios, roles, permisos, sesiones, OTP, accesos y registros pendientes |
| `catalogo` | Tipos de limpieza y de hogar, elementos, zonas, tareas, feriados y franjas |
| `crm` | Clientes, direcciones, empresas y lista de espera |
| `personal` | Empleados, horarios, documentos, capacitaciones e insumos |
| `operacion` | Solicitudes, ofertas, checklist, recorrido, chat y soporte |
| `finanzas` | Pagos, planilla, liquidaciones, adelantos y comprobantes |
| `comercial` | Combos, promociones, cupones, puntos y referidos |
| `contenido` | Textos del sitio, preguntas frecuentes, testimonios y postulaciones |
| `sistema` | Parámetros, notificaciones, auditoría, archivos e idempotencia |

## Seguridad

El acceso es con cédula y contraseña. La cédula son de 5 a 10 dígitos, sin puntos. La contraseña propia pide al menos 8 caracteres, una letra, un número y que no sea igual a la cédula.

Hay dos altas distintas:

- Si la crea un administrador, la contraseña inicial es la cédula, `debe_cambiar_password` queda en verdadero y vence a las `password_inicial_horas` (72). Mientras esa marca esté activa, el resto de la API responde 403 `PASSWORD_CHANGE_REQUIRED`. Si venció, la recuperación es por OTP y el código de error es `PASSWORD_EXPIRED`.
- Si la persona se registra sola, elige su contraseña. El alta queda pendiente hasta verificar el código de 6 dígitos. En desarrollo ese código vuelve en la respuesta.

El login fallido responde siempre `CREDENCIALES` con el texto "Cédula o contraseña incorrecta.", también si la cédula no existe. En ese caso igual se verifica un hash Argon2 de relleno. A los `intentos_maximos` (5) la cuenta se bloquea `bloqueo_minutos` (15). A los `captcha_tras_fallos` (3), por IP o por cédula, el login pide captcha. El cupo de intentos vive en `seguridad.rate_limits`.

El access token dura 15 minutos y va en `Authorization: Bearer`. El refresh rota: se guarda solo el SHA-256, y reusar un token viejo de la misma familia revoca la familia. En la web la cookie es HttpOnly y SameSite=Strict, en la ruta `/api/v1/auth`. El cuerpo trae `refresh_token` solo si el header `X-Client` es `mobile` o `capacitor`.

Los permisos salen del rol guardado en la base, no del campo `rol` del token. Un access token firmado que diga `superadmin` pero pertenezca a un cliente responde 403. Un token sin firma, o con algoritmo `none`, responde 401.

El superadmin tiene todos los permisos. El admin tiene todos menos `usuarios.admin` y `parametros.criticos`. Esos cuatro parámetros críticos son `margen_piso_absoluto`, `iva_divisor`, `password_inicial_horas` e `intentos_maximos`.

Bloquear un cliente marca `seguridad.usuarios.estado` en `bloqueado`. Aunque el access token siga vigente, la API responde 403 `CLIENTE_BLOQUEADO`. Un empleado suspendido responde 403 `EMPLEADO_SUSPENDIDO`.

## Precios

Nada de la fórmula está fijo en el código como fuente de verdad. Las tarifas están en el catálogo y el resto en `sistema.parametros`. Un cambio queda en el historial. También se puede programar un valor para una fecha.

Los porcentajes de combos, promociones y cupones se guardan en puntos: 10 es 10%. Los factores como `margen_minimo` (0.35) o `recargo_nocturno` (1.25) son multiplicadores directos.

El cálculo, en `app/domain/precios.py`:

1. Horas = máximo entre `horas_base × factor + minutos de elementos / 60` y `minimo_horas` (2).
2. Subtotal = tarifa por hora × horas + adicionales de elementos. Cada elemento pide una cantidad entera de 1 a 30. Una cantidad negativa o un texto responde 422 `ELEMENTO` y no entra en la fórmula.
3. Los descuentos se aplican antes de los multiplicadores, en este orden de origen: combo, mejor oferta automática, cupón y puntos. Solo se acumulan si todos los elegidos son acumulables. Si no, queda el de mayor monto. El tope es `tope_descuento_orden` (30% del subtotal) y también existe un tope mensual por cliente.
4. Total = (subtotal − descuentos) × multiplicador de horario × urgencia × preferencia de empleado + traslado.
5. El IVA está incluido: total / `iva_divisor` (11).
6. Si el margen quedaría bajo `margen_minimo` (35%), se recorta el descuento desde el último aplicado. El piso absoluto de una campaña aprobada es `margen_piso_absoluto` (20%).

El multiplicador de horario sale del feriado, si no de la franja nocturna, si no del fin de semana, si no de la franja pico. La franja valle es un descuento, no un recargo. La semilla usa valle de 09 a 11 los días hábiles (10%), pico de 17 a 20 (×1.15) y nocturno de 20 a 06 (×1.25).

La zona es el polígono activo más chico que contiene el punto. El traslado de esa zona se suma al final. Si el punto no cae en ninguna zona, la cotización sale de cobertura y el cliente puede anotarse en la lista de espera.

La cotización pública estima el descuento de primera compra. La del cliente usa su historial real. Las respuestas que no son de administración no incluyen margen ni costo.

El costo interno, usado para el margen y la planilla, es `pago_empleado_hora` × horas + cargas (`cargas_porcentaje`) + insumos (`insumos_por_hora` × horas) + traslado. La semilla deja `pago_empleado_hora` en 20.000 para que las tarifas de ejemplo superen el piso de 35%. El documento de requerimientos muestra 25.000: se cambia en parámetros, no en el código.

Tarifas iniciales, en guaraníes por hora, y el factor de horas: regular 45.000 / 1.0, profunda 70.000 / 1.6, post-obra 90.000 / 2.2, oficina 60.000 / 1.2. Los hogares de ejemplo son 2, 4 y 6 horas.

## Solicitudes

Los estados no llevan tilde: `BORRADOR`, `ENVIADA`, `EN_REVISION`, `ASIGNADA`, `CONFIRMADA`, `EN_CAMINO`, `EN_CURSO`, `COMPLETADA`, `PAGADA`, `CALIFICADA`, y además `CANCELADA`, `NO_SHOW` y `DISPUTA`. Las transiciones permitidas están en `app/domain/estados.py`. Cada cambio escribe un evento.

Hay un solo borrador por cliente. Enviar exige hogar, tipo, dirección, fecha y hora, y que ese horario tenga un empleado libre en la zona. El número es `LS-{año}-{secuencia}`.

El pago del alta tiene que cubrir el anticipo (`anticipo_porcentaje`, 30%). En desarrollo, con `SIMULAR_PAGOS=true`, tarjeta, transferencia, billetera y efectivo quedan confirmados al crearlos. El saldo descuenta `credito_saldo`. Si el pago cubre el total, al terminar el servicio la orden pasa a `PAGADA`, se emite el comprobante interno, se suman puntos y se acredita el referido. El PDF sale por `GET /api/v1/cliente/facturas/{id}/pdf`. `POST /api/v1/admin/facturas/{id}/emitir` marca el CDC cuando haya SIFEN. Sin proveedor, el comprobante es interno.

`POST /api/v1/pagos/webhook` confirma un pago pendiente si el secreto coincide. Sin secreto responde 401. Un cuerpo que no trae un `pago_id` válido responde 422. Un pago que no existe responde 404. Si el pago ya estaba confirmado, devuelve ese pago.

El cliente solo puede pagar `anticipo`, `saldo` o `total`. La propina la registra la calificación. Un tipo `reembolso`, un monto que no sea mayor a cero o un medio que no esté activo responden 422. El reembolso lo calcula la cancelación, no un pago armado por el cliente.

La cancelación usa el parámetro `cancelacion`: más de 24 horas sin cargo, entre 4 y 24 horas un cargo del 30% con compensación del 50% al empleado, y menos de 4 horas cargo total. Tres cancelaciones del empleado en el mes (`cancelaciones_suspension_mes`) lo suspenden. Esas cancelaciones se cuentan en `personal.sanciones`, no en un flag de la orden, porque al cancelar el empleado la solicitud vuelve a `EN_REVISION`.

La garantía se pide si la nota es como máximo `garantia_nota_max` (3), dentro de `garantia_horas` (24) y sin pasar `garantia_tope_cliente` (2). Cada solicitud admite una sola re-limpieza. Pedirla de nuevo responde 422.

Una disputa solo existe si el estado puede pasar a `DISPUTA` (`EN_CURSO`, `COMPLETADA` o `PAGADA`). Desde un borrador o una orden ya calificada responde 409 y no se guarda el incidente. La descripción tiene que tener al menos 5 caracteres. El cambio queda en `operacion.orden_eventos`.

La agenda pide entre 1 y 12 horas. Cero o un número negativo responde 422 `HORAS`.

Crear la orden, el check-in, el check-out y el pago aceptan `Idempotency-Key`. La misma persona, con la misma clave y el mismo cuerpo, recibe la respuesta guardada. Otra persona, o el mismo usuario con otro cuerpo, recibe 409 y no ve la orden ajena.

## Asignación

El puntaje es cercanía 40%, calificación 30%, disponibilidad 20% y carga 10%. Los pesos son parámetros. La cercanía usa la última ubicación del empleado o, si no hay, el centro de su zona, contra la dirección. La distancia de referencia es `distancia_max_m` (20 km). Si en 30 días rechazó más que `umbral_rechazo` (30%) de las ofertas, el puntaje se reduce a la mitad.

Se ofrece de a una persona, por `tiempo_aceptacion_min` (30). La asignación automática corre si `asignacion_automatica` es verdadero o si la orden es de emergencia. Hoy la semilla lo deja en falso, así que la cola espera al panel o a `POST /admin/ordenes/{id}/asignar-auto`. La asignación manual pide un motivo cuando reemplaza a alguien ya confirmado.

Entra al ranking quien está activo, cubre la zona, tiene horario que contiene el servicio, no está bloqueado y no pisa otra orden. Entre servicios se reserva `buffer_traslado_min` (30). La agenda ofrece slots de `slot_minutos` (60) entre `horario_atencion_desde` (07:00) y `horario_atencion_hasta` (20:00).

## Recorrido del servicio

Confirmada, el empleado marca que va en camino. El check-in y el check-out tienen que caer dentro de `radio_checkin_m` (150) de la dirección. Más lejos, solo se acepta con nota y foto, y queda para revisión del panel. Si el check-in pasa `alerta_demora_min` (15) de la hora pactada, se avisa.

Para cerrar hay que completar las tareas obligatorias del tipo de limpieza y subir la foto del después. Si el tipo tiene `foto_antes_obligatoria`, también la del antes. Profunda y post-obra la piden. La foto tiene que ser un archivo subido por ese empleado. El archivo de otra persona responde 403, y un id que no existe responde 404. Al cerrar se calcula la planilla con las horas estimadas más las extras aprobadas, y los bonos de puntualidad y de calificación si corresponden.

El cliente y el empleado asignado se califican después del cobro. Otro empleado recibe 404. Hay una sola nota por orden y por rol: la segunda responde 409. La nota va de 1 a 5. Si a los `plazo_calificacion_dias` (7) nadie calificó, el cierre automático lo hace el ciclo de mantenimiento.

## Mapas y zonas

El proyecto no usa PostGIS. El polígono se guarda como GeoJSON `[lng, lat]`. `catalogo.punto_en_poligono` hace ray casting y `catalogo.distancia_metros` hace haversine. Python repite esas dos cuentas en `app/domain/geo.py`.

El viaje estimado es la distancia en línea recta por `factor_ruta` (1.3), a `velocidad_urbana_kmh` (30). Si `GOOGLE_MAPS_SERVER_KEY` tiene valor, se usa Distance Matrix y el resultado se cachea en `sistema.cache_geo`.

La hora del negocio es `America/Asuncion`. En Windows hace falta el paquete `tzdata`.

## Tiempo real y avisos

Los WebSocket están en `/api/v1/ws/ordenes/{orden_id}`, `/api/v1/ws/admin/mapa` y `/api/v1/ws/empleado`, con el access token en el query `token`. El hub es en memoria: un solo proceso.

WhatsApp, SMS y correo no están conectados a un proveedor. Cada aviso se guarda en `sistema.notificaciones` y, en desarrollo, el OTP vuelve en la respuesta. Las plantillas están en `sistema.plantillas_notificacion`.

## Mantenimiento

Cada 30 segundos, desde que arranca el proceso: vencen ofertas, se aplican parámetros programados, se purgan ubicaciones viejas según `retencion_ubicacion_dias` (90), se cierran calificaciones vencidas y se generan las órdenes de los planes recurrentes que tocan.

## Catálogo de la API

Son 159 operaciones HTTP, las de `/openapi.json`, más 3 WebSocket que Swagger no lista. El prefijo es `/api/v1`. En `/docs`, el botón Authorize recibe el `access_token` de `POST /auth/login` (sin la palabra Bearer: Swagger la agrega).

Las operaciones de cliente, empleado y panel exigen sesión. El panel además exige el permiso del rol. El superadmin tiene todos. El admin tiene todos menos `usuarios.admin` y `parametros.criticos`.

### Sistema

| Método | Ruta | Quién | Qué hace |
| --- | --- | --- | --- |
| GET | `/salud` | público | Responde `{"ok": true, "servicio": "limsys"}`. |

### Pagos

| Método | Ruta | Quién | Qué hace |
| --- | --- | --- | --- |
| POST | `/pagos/webhook` | cabecera `X-Webhook-Secret` | Confirma un pago pendiente. Si `SIMULAR_PAGOS` ya lo confirmó, devuelve ese pago. |

### Autenticación

| Método | Ruta | Quién | Qué hace |
| --- | --- | --- | --- |
| POST | `/auth/login` | público | Entra con cédula y contraseña. Devuelve access token. El refresh va en cookie HttpOnly, o en el cuerpo si `X-Client` es `mobile` o `capacitor`. |
| POST | `/auth/cambiar-password` | sesión | Cambia la clave. Es el único camino de negocio mientras `debe_cambiar_password` está activo. |
| POST | `/auth/recuperar` | público | Pide un OTP de 6 dígitos a la cédula. En desarrollo el código vuelve en `debug_codigo`. |
| POST | `/auth/verificar-otp` | público | Canjea el OTP y deja una sesión para elegir la clave nueva. |
| POST | `/auth/refresh` | cookie o cuerpo | Rota el refresh. Reusar uno viejo de la misma familia revoca la familia. |
| POST | `/auth/logout` | sesión | Cierra la sesión actual. |
| POST | `/auth/logout-todos` | sesión | Revoca todas las sesiones del usuario. |
| GET | `/auth/yo` | sesión | Devuelve el usuario, el rol y los permisos. |
| GET | `/auth/sesiones` | sesión | Lista los dispositivos con sesión abierta. |
| DELETE | `/auth/sesiones/{sesion_id}` | sesión | Cierra una sesión puntual. |
| POST | `/auth/telefono/enviar` | sesión | Manda un OTP al teléfono guardado en la cuenta. |
| POST | `/auth/telefono/verificar` | sesión | Marca el teléfono como verificado. |
| POST | `/auth/webauthn/registro/opciones` | sesión | Arma el reto para registrar huella o Face ID. |
| POST | `/auth/webauthn/registro` | sesión | Guarda la credencial. Una credencial vacía responde 422 `WEBAUTHN`. |
| POST | `/auth/webauthn/login/opciones` | público | Arma el reto de ingreso. Sin credencial guardada responde 422. |
| POST | `/auth/webauthn/login` | público | Entra con la credencial. Sin dispositivo real no se puede completar. |

### Sitio público

Ninguna pide sesión.

| Método | Ruta | Qué hace |
| --- | --- | --- |
| GET | `/publico/tema` | Colores y banderas `autoregistro` y `login_whatsapp`. Query `idioma`, por defecto `es`. |
| GET | `/publico/contenido` | Textos del sitio para ese idioma. |
| GET | `/publico/servicios` | Tipos de limpieza, hogares y elementos, con tarifa y factor. |
| GET | `/publico/combos` | Combos vigentes y visibles en la web. |
| GET | `/publico/ofertas` | Promociones vigentes. |
| GET | `/publico/zonas` | Zonas activas, con polígono y traslado. |
| GET | `/publico/faq` | Preguntas frecuentes. |
| GET | `/publico/testimonios` | Testimonios curados y calificaciones reales de 4 o más. |
| GET | `/publico/cobertura` | `lat` y `lng` obligatorios. Dice si el punto cae en una zona y el traslado. |
| POST | `/publico/cotizar` | Precio estimado sin historial del cliente. Incluye la estimación de primera compra. No devuelve margen. |
| POST | `/publico/registro` | Deja el alta pendiente y manda el OTP. Exige términos y contraseña propia. |
| POST | `/publico/registro/verificar` | Confirma el OTP y crea la cuenta. |
| POST | `/publico/contacto` | Guarda un mensaje de contacto. |
| POST | `/publico/empresas/cotizacion` | Guarda un pedido de empresa. |
| POST | `/publico/postulaciones` | Guarda una postulación de empleado. |
| POST | `/publico/lista-espera` | Anota una dirección fuera de cobertura. |
| GET | `/publico/seguimiento/{token}` | Estado de la orden para quien tenga el enlace, sin sesión. |
| GET | `/publico/legal/{clave}` | Texto legal. La clave de ejemplo es `terminos`. |
| POST | `/publico/coordenadas` | Saca latitud y longitud de una URL de mapa. Query `url`. |
| GET | `/publico/fecha` | Fecha y hora de `America/Asuncion`. |

### Cliente

| Método | Ruta | Qué hace |
| --- | --- | --- |
| GET | `/cliente/inicio` | Resumen: próxima orden, puntos y avisos sin leer. |
| POST | `/cliente/tour` | Marca el recorrido de bienvenida como visto. |
| GET | `/cliente/perfil` | Nombre, contacto e idioma. |
| PUT | `/cliente/perfil` | Actualiza esos datos. El correo tiene que ser un dominio válido. |
| GET | `/cliente/fiscal` | RUC y razón social. |
| PUT | `/cliente/fiscal` | Los guarda para el comprobante. |
| GET | `/direcciones` | Direcciones del cliente, con zona. |
| POST | `/direcciones` | Crea una. Si el punto no tiene zona, la API lo rechaza. |
| PATCH | `/direcciones/{direccion_id}` | Edita apodo, referencia, piso o instrucciones. |
| DELETE | `/direcciones/{direccion_id}` | La da de baja. |
| GET | `/disponibilidad` | Slots libres. `fecha` es obligatoria. También `horas`, `lat` y `lng` cuando no hay dirección. |
| POST | `/presupuesto` | Precio con el historial real del cliente. No incluye margen. |
| POST | `/cupones/validar` | Dice si el código aplica a ese subtotal. |
| PUT | `/ordenes/borrador` | Guarda el único borrador del cliente. |
| POST | `/ordenes` | Envía la solicitud. El pago tiene que cubrir el anticipo. Acepta `Idempotency-Key`. |
| GET | `/ordenes` | Lista las órdenes del cliente. |
| GET | `/ordenes/{orden_id}` | Detalle, precio visible y estado. |
| GET | `/ordenes/{orden_id}/seguimiento` | Línea de tiempo y ubicación del empleado si ya salió. |
| POST | `/ordenes/{orden_id}/compartir` | Genera el token del enlace público. |
| POST | `/ordenes/{orden_id}/cancelar` | Cancela según la ventana de `cancelacion`. |
| POST | `/ordenes/{orden_id}/calificar` | Nota, categorías, comentario y propina. Solo después del cobro. |
| POST | `/ordenes/{orden_id}/repetir` | Arma otra solicitud con los mismos datos. |
| POST | `/ordenes/{orden_id}/pagos` | Anticipo, saldo o propina. Acepta `Idempotency-Key`. |
| POST | `/ordenes/{orden_id}/extra-tiempo/{extra_id}/responder` | Aprueba o rechaza minutos extra. |
| POST | `/ordenes/{orden_id}/garantia` | Pide re-limpieza si la nota y el plazo entran en la regla. |
| POST | `/ordenes/{orden_id}/problemas` | Abre una disputa. |
| POST | `/ordenes/{orden_id}/conformidad` | Cierra la disputa del lado del cliente. |
| GET | `/ordenes/{orden_id}/mensajes` | Chat de la orden. |
| POST | `/ordenes/{orden_id}/mensajes` | Escribe en ese chat. |
| GET | `/cliente/facturas` | Comprobantes del cliente. |
| GET | `/cliente/facturas/{factura_id}` | Un comprobante. |
| GET | `/cliente/facturas/{factura_id}/pdf` | El PDF. |
| GET | `/cliente/puntos` | Saldo y movimientos. |
| GET | `/cliente/referidos` | Código propio y acreditaciones. |
| GET | `/cliente/planes` | Planes recurrentes. |
| POST | `/cliente/planes` | Crea uno: dirección, tipo, frecuencia, día y hora. |
| PATCH | `/cliente/planes/{plan_id}` | Pausa o reactiva. Query `estado`. |
| GET | `/cliente/notificaciones` | Avisos guardados. |
| POST | `/cliente/notificaciones/leer-todas` | Los marca leídos. |
| PUT | `/cliente/notificaciones/preferencias` | Push, WhatsApp, SMS y correo. |
| POST | `/notificaciones/suscripcion` | Guarda la suscripción push del dispositivo. |
| GET | `/soporte/tickets` | Tickets del cliente. |
| POST | `/soporte/tickets` | Abre uno. |
| POST | `/soporte/tickets/{ticket_id}/mensajes` | Responde en el ticket. |

### Empleado

| Método | Ruta | Qué hace |
| --- | --- | --- |
| GET | `/empleado/inicio` | Próximo servicio, ofertas y si puede aceptar (capacitación al día). |
| PATCH | `/empleado/disponibilidad` | Enciende o apaga `disponible`. Query `disponible`. |
| GET | `/empleado/ofertas` | Ofertas vigentes para esta persona. |
| POST | `/empleado/ofertas/{oferta_id}/aceptar` | Acepta. Falla con `CAPACITACION` si falta un curso obligatorio. |
| POST | `/empleado/ofertas/{oferta_id}/rechazar` | Rechaza. Entra en el umbral de rechazo del ranking. |
| GET | `/empleado/agenda` | Servicios entre `desde` y `hasta`. |
| POST | `/empleado/bloqueos` | Bloquea un rango de la agenda. |
| DELETE | `/empleado/bloqueos/{bloqueo_id}` | Quita ese bloqueo. |
| PUT | `/empleado/horarios` | Reemplaza la semana. El día es ISO: 1 lunes, 6 sábado. |
| POST | `/ordenes/{orden_id}/en-camino` | Pasa la orden a `EN_CAMINO`. |
| POST | `/ordenes/{orden_id}/check-in` | Llegada. Fuera de `radio_checkin_m` exige nota y foto, y queda en revisión. Acepta `Idempotency-Key`. |
| POST | `/ordenes/{orden_id}/check-out` | Cierra el servicio. Exige tareas obligatorias y foto del después. Acepta `Idempotency-Key`. |
| PATCH | `/ordenes/{orden_id}/tareas/{tarea_id}` | Marca una tarea del checklist. |
| POST | `/ordenes/{orden_id}/extra-tiempo` | Pide minutos extra. El cliente los aprueba. |
| POST | `/ordenes/{orden_id}/fotos` | Asocia una foto `antes` o `despues`. Query `tipo` y `archivo_id`. |
| POST | `/empleado/ordenes/{orden_id}/cancelar` | El empleado devuelve la orden a `EN_REVISION`. Cuenta para la suspensión mensual. |
| POST | `/empleado/ordenes/{orden_id}/calificar-cliente` | Califica al cliente después del cobro. |
| POST | `/empleado/sos` | Aviso de emergencia, con la última ubicación. |
| POST | `/empleado/ubicacion` | Guarda latitud y longitud para el mapa y el ranking. |
| GET | `/empleado/ingresos` | Horas, bonos y propinas del período. |
| GET | `/empleado/liquidaciones` | Liquidaciones propias. |
| POST | `/empleado/adelantos` | Pide un adelanto. Se descuenta en la liquidación. |
| GET | `/empleado/capacitaciones` | Cursos y cuáles ya completó. |
| POST | `/empleado/capacitaciones/{capacitacion_id}/completar` | Marca el curso como hecho. |
| GET | `/empleado/documentos` | Documentos subidos y su estado. |
| POST | `/empleado/documentos` | Asocia un archivo. Query `tipo` y `archivo_id`. |
| GET | `/empleado/insumos` | Catálogo de insumos y pedidos propios. |
| POST | `/empleado/insumos/solicitudes` | Pide reposición. |
| POST | `/empleado/ausencias` | Pide un día libre. El panel lo aprueba. |
| POST | `/empleado/ordenes/{orden_id}/cambio-turno` | Ofrece el turno a otro empleado. |
| GET | `/empleado/ranking` | Puesto, nota y servicios completados. |
| PUT | `/empleado/preferencias` | Avisos y preferencias de la app del empleado. |

### Administración

| Método | Ruta | Qué hace |
| --- | --- | --- |
| GET | `/admin/solicitudes` | Cola: órdenes en revisión y las recién enviadas. |
| POST | `/admin/ordenes/{orden_id}/asignar` | Asigna a un empleado. Si no es el primero del ranking, hace falta `motivo`. |
| POST | `/admin/ordenes/{orden_id}/asignar-auto` | Corre el ranking y ofrece al primero. |
| POST | `/admin/ordenes/{orden_id}/reasignar` | Saca al empleado actual y vuelve a ofrecer. |
| GET | `/admin/ordenes/{orden_id}/candidatos` | Ranking con el puntaje desglosado. |
| GET | `/admin/mapa` | Empleados con ubicación reciente y órdenes del día. |
| POST | `/admin/llegadas/{llegada_id}/revisar` | Aprueba o rechaza un check-in fuera de radio. Query `aprobar`. |
| GET | `/admin/clientes` | Lista de clientes. |
| POST | `/admin/clientes` | Alta. La contraseña inicial es la cédula. |
| POST | `/admin/clientes/{cliente_id}/bloquear` | Bloquea o desbloquea. Query `bloqueado`. |
| POST | `/admin/clientes/{cliente_id}/restablecer` | Reinicia la clave a la cédula y obliga a cambiarla. |
| GET | `/admin/empleados` | Lista, con nota y disponibilidad. |
| POST | `/admin/empleados` | Alta. La contraseña inicial es la cédula. Hay que indicar las zonas. |
| POST | `/admin/empleados/{empleado_id}/sancionar` | Registra una sanción. Una grave puede suspender. |
| GET | `/admin/finanzas/planilla` | Pagos y costos del período. |
| POST | `/admin/finanzas/pagos/{pago_id}/confirmar` | Confirma un pago que no pasó por la simulación. |
| POST | `/admin/liquidaciones/generar` | Arma la liquidación. Query `empleado_id`, `desde` y `hasta`. |
| POST | `/admin/liquidaciones/{liquidacion_id}/pagar` | La marca pagada. |
| GET | `/admin/parametros` | Parámetros vigentes. |
| PUT | `/admin/parametros` | Cambia uno y deja historial. Los cuatro críticos piden superadmin. |
| GET | `/admin/parametros/historial` | Cambios anteriores. |
| POST | `/admin/parametros/programar` | Deja un valor para `vigente_desde`. El mantenimiento lo aplica. |
| GET | `/admin/combos` | Combos, también los pausados. |
| POST | `/admin/combos` | Crea uno. El porcentaje va en puntos. |
| POST | `/admin/combos/{combo_id}/pausar` | Lo activa o pausa. Query `activo`. |
| POST | `/admin/promociones/simular` | Muestra el precio de un combo de ejemplo. Query `combo_id`. |
| POST | `/admin/promociones` | Crea una. El `tipo` es uno de: `primera_compra`, `horario_valle`, `recurrencia`, `oferta_flash`, `cumpleanos`, `empresas`, `campana_zona`, `reactivacion`, `cuarta_consecutiva`. |
| POST | `/admin/cupones` | Crea un cupón, con tope de monto y de usos. |
| POST | `/admin/cupones/masivo` | Genera varios. Query `prefijo` y `cantidad`. |
| GET | `/admin/reportes/kpis` | Indicadores del período. |
| GET | `/admin/reportes/ordenes` | Listado. `formato=csv` lo baja como archivo. |
| GET | `/admin/reportes/promociones` | Usos y descuento por promoción. |
| GET | `/admin/incidentes` | Disputas abiertas y resueltas. |
| POST | `/admin/incidentes/{incidente_id}/resolver` | Cierra la disputa, con resolución y monto si corresponde. |
| GET | `/admin/auditoria` | Accesos y cambios sensibles. |
| GET | `/admin/contenido` | Textos editables del sitio. |
| PUT | `/admin/contenido` | Guarda una clave de contenido. |
| POST | `/admin/usuarios` | Alta de otro admin. Solo superadmin. |
| GET | `/admin/postulaciones` | Postulaciones recibidas. |
| PATCH | `/admin/postulaciones/{postulacion_id}` | Cambia el estado. Query `estado`. |
| POST | `/admin/facturas/{factura_id}/emitir` | Guarda el CDC cuando haya SIFEN. |
| POST | `/admin/ausencias/{ausencia_id}/resolver` | Aprueba o rechaza. Query `aprobar`. |
| POST | `/admin/cambios-turno/{cambio_id}/resolver` | Aprueba o rechaza el cambio. Query `aprobar`. |

### Archivos

| Método | Ruta | Quién | Qué hace |
| --- | --- | --- | --- |
| POST | `/archivos` | sesión | Sube jpg, png, webp o pdf, hasta 8 MB. |
| GET | `/archivos/{archivo_id}` | dueño o panel | Devuelve el archivo. Otra persona recibe 403. |

### Tiempo real

No aparecen en Swagger. El access token va en el query `token`.

| Ruta | Quién | Qué hace |
| --- | --- | --- |
| `/ws/ordenes/{orden_id}` | cliente o empleado de esa orden | Estados, chat y ubicación de una orden. |
| `/ws/admin/mapa` | panel | Mapa en vivo. |
| `/ws/empleado` | empleado | Ofertas y avisos de su jornada. |

El hub es en memoria, de un solo proceso. El método para publicar es `emitir`.

## Variables

Están en `.env`, con el modelo en `.env.example`.

| Variable | Uso |
| --- | --- |
| `DATABASE_URL` | Conexión PostgreSQL |
| `JWT_SECRET` | Firma de access, refresh y OTP |
| `ENV` | `development` habilita OTP en la respuesta y el modo local |
| `CORS_ORIGINS` | Orígenes de la web. La semilla incluye 4200 y 4300 |
| `DEV_RETURN_OTP` | Devuelve `debug_codigo` |
| `SIMULAR_PAGOS` | Confirma tarjeta, transferencia, billetera y efectivo |
| `CAPTCHA_DEV_MODE` / `CAPTCHA_DEV_TOKEN` | Captcha fijo de desarrollo |
| `ADMIN_CEDULA` / `ADMIN_PASSWORD` | Primer superadmin |
| `WEBHOOK_SECRET` | Secreto de `POST /pagos/webhook` |
| `GOOGLE_MAPS_SERVER_KEY` | Vacío usa la estimación local |
| `STORAGE_DIR` | Carpeta de archivos |
| `RATE_LIMIT_LOGIN` | Tope de logins por ventana |

## Pruebas

```powershell
.\.venv\Scripts\python.exe -m pytest tests -q
```

Las tres pruebas usan la base local, no un doble.

`tests/test_precios.py` cubre la fórmula, el mínimo de horas, el recorte de margen y el polígono.

`tests/test_api.py` recorre salud, cotización pública, alta de cliente con cambio obligatorio de clave, y una jornada completa creada por la API: empleado con horario, pago, asignación, check-in fuera de radio, checklist, foto, check-out, PDF y KPIs.

`tests/test_qa_agresivo.py` es el circuito que intenta romper la API y después la recorre entera. Crea sus propias personas y no usa las cuentas de demostración. Comprueba, en este orden:

1. Rechazos: sin sesión, cédula con SQL, cuerpo vacío, token sin firma, cantidad negativa o texto, punto fuera de cobertura, cobertura sin coordenadas, enlace de seguimiento inexistente, registro sin términos, contraseña débil, webhook sin secreto, webhook con cuerpo roto y pago inexistente.
2. Aislamiento: un cliente no entra al panel, un token con rol falsificado no escala, un admin no cambia un parámetro crítico, no se edita la dirección de otro, no se descarga el archivo de otro y no se lista la orden ajena.
3. Jornada feliz del 2027-09-07: presupuesto sin margen, pago total, la misma clave de idempotencia no duplica la orden, otra persona con esa clave recibe 409, asignación, aceptación, check-in fuera de radio, checklist, foto propia, check-out, nota 5, nota del empleado, PDF y seguimiento público. La garantía y una disputa posterior quedan rechazadas porque la orden ya está `CALIFICADA`.
4. Cancelación del 2027-09-08: se cancela una vez y la segunda, igual que salir en camino después, responde 409.
5. Garantía del 2027-09-09: nota 2, una re-limpieza en `EN_REVISION` y el segundo pedido responde 422.
6. El cliente de contraste queda bloqueado y su token vigente responde 403 `CLIENTE_BLOQUEADO`.
