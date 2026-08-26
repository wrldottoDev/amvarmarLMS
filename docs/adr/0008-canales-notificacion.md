# ADR-0008: Canales de notificación por evento

- **Fecha:** 2026-08-19
- **Estado:** Aprobado
- **Aprobado por:** Otoniel González (revisión técnica) + AMVARMAR (decisión de negocio)

## Contexto

Canales disponibles: `IN_APP`, `EMAIL`, `PUSH`. Hay que decidir, evento por evento (nuevo shipment, cambio de
estado, requisito abierto, despacho aprobado, etc.), qué canales aplican por defecto y si el cliente puede
desactivar alguno.

## Decisión

**Canales:**

| Canal | Estado | Control del usuario |
|---|---|---|
| `IN_APP` | Activo desde el inicio | Ninguno — siempre encendido, no tiene fila de preferencia |
| `EMAIL` | Activo desde el inicio, **obligatorio** | Ninguno — no se puede desactivar. Va al correo verificado de la cuenta. |
| `WHATSAPP` | Fase futura, no se construye en el alcance actual | Activar/desactivar canal completo + desactivar eventos no críticos individualmente, tras verificar número |

`PUSH` (FCM/APNs) del documento de arquitectura original queda fuera de este catálogo. **Resuelto en
ADR-0010:** sí se construye, pero recién en la fase de aplicaciones móviles (después de Fase 5), no en Fase 4.
Cuando arranque esa fase, `PUSH` se suma como cuarto canal con las mismas reglas de los eventos críticos ya
definidos aquí (no desactivable para críticos, igual que `WHATSAPP`).

**Reglas de `EMAIL`:**
- Nunca incluye documentos adjuntos ni información sensible completa — solo un enlace para entrar al sistema.
  Regla dura para el diseño de plantillas de Paso 4.2: ningún dato de negocio en el cuerpo del correo, solo
  contexto mínimo + link.
- Errores de entrega y reintentos quedan registrados (`notification_deliveries`, ya definida en el documento
  de arquitectura, sección 3.8 — sin cambios de esquema).

**Reglas de `WHATSAPP` (cuando se construya, fase futura, fuera del alcance de Fase 4 actual):**
- Requiere número verificado (flujo análogo a `EMAIL_VERIFY` de `one_time_tokens` — nuevo propósito
  `PHONE_VERIFY`).
- El usuario controla: (a) el canal completo (activar/desactivar), (b) eventos no críticos, uno por uno.
- Si está activo, los eventos críticos llegan también por WhatsApp y **no se pueden apagar individualmente**
  por ese canal — el único apagado posible de un crítico es apagar WhatsApp entero (con lo cual deja de llegar
  por ahí, pero sigue llegando por `IN_APP` + `EMAIL`, que son obligatorios siempre).

**Alcance del control del usuario — una sola frase que resuelve toda ambigüedad:** el usuario únicamente puede
controlar WhatsApp (canal completo + eventos no críticos por ese canal). `IN_APP`, `EMAIL` y los eventos
críticos por cualquier canal habilitado son siempre obligatorios, sin excepción, sin preferencia que los
apague. Por eso el modelo de preferencias no necesita filas para `IN_APP`/`EMAIL` — solo existen preferencias
`WHATSAPP`.

**Catálogo de eventos críticos** (siempre `IN_APP` + `EMAIL`; + `WHATSAPP` si el usuario lo tiene activo; nunca
desactivables):

| # | Evento | Disparador técnico ya definido |
|---|---|---|
| 1 | Falta un documento obligatorio que bloquea el despacho | `shipment_requirements` tipo `DOCUMENT` en `PENDING`/`OPEN` al intentar `PREPARING→DISPATCHED` (ADR-0003) |
| 2 | Documento rechazado | Requisito documental pasa a `REJECTED` (ADR-0003) |
| 3 | Carga que podría requerir permiso o inspección | `shipment.permit_review_required = true` (ADR-0003) |
| 4 | Solicitud de despacho aprobada, rechazada o con problemas | `dispatch_requests.approve` / `.reject` (Paso 3.3) |
| 5 | Confirmación de que una carga fue despachada | Transición `PREPARING→DISPATCHED` (ADR-0001) |
| 6 | Confirmación de entrega | Transición `DISPATCHED→DELIVERED` (ADR-0006) |
| 7 | Cambios o correcciones importantes en el estado | Transición backward, cancelación, reapertura, reversión de `DELIVERED` (ADR-0001) |
| 8 | Alertas de seguridad de la cuenta | Login desde sesión nueva, reuse detection de refresh (revocación de sesión), cambio de contraseña (Paso 1.6/1.7) |

**Eventos no críticos:** todo lo que no está en la tabla de arriba (ej. progreso rutinario de timeline,
verificación exitosa de un documento sin rechazo previo, creación de prealerta), **más un caso explícito**:

- **Documentos por archivar** (ADR-0007) — aviso antes de que los archivos se recompriman por retención de
  6 meses. Confirmado como **no crítico**: el usuario puede desactivarlo individualmente por WhatsApp cuando
  ese canal exista. Sigue llegando por `IN_APP` + `EMAIL` igual que cualquier evento, crítico o no.

Van por `IN_APP` + `EMAIL` igual que los críticos — la diferencia no crítico/crítico solo importa para
**WhatsApp**: son los únicos que el usuario puede apagar individualmente por ese canal.

## Alternativas consideradas

- Permitir desactivar `EMAIL` como canal general. Descartada explícitamente por la decisión — es el canal de
  respaldo que garantiza que el usuario se entera aunque no tenga la plataforma abierta ni WhatsApp activo.
- Incluir push (FCM) en este catálogo ahora, aprovechando que `device_tokens`/`firebase-admin` ya están en el
  alcance técnico del proyecto. Descartada: ADR-0010 confirmó que no hay app móvil hasta después de Fase 5 —
  construir push sin app que lo consuma sigue siendo trabajo especulativo, ahora confirmado, no solo probable.

## Consecuencias

- **Paso 4.2 del plan reduce su alcance real de construcción a `IN_APP` + `EMAIL` únicamente** para el primer
  lanzamiento — el plan original lo tenía titulado "Correo, push y bandeja in-app" asumiendo FCM como segundo
  canal; con ADR-0010 confirmado, push se construye en la fase de apps móviles (después de Fase 5) y WhatsApp
  queda pospuesto a una fase futura sin numerar todavía.
- Se agrega un flujo de verificación de número de teléfono (`PHONE_VERIFY`) al catálogo de `one_time_tokens`
  cuando se construya WhatsApp — no en el alcance actual de Fase 4.
- Tabla `notification_preferences(user_id, event_code, enabled)` solo aplica a canal `WHATSAPP` — no existen
  filas de preferencia para `IN_APP`/`EMAIL`, así que la interfaz (Fase F) no debe ni mostrar el toggle para
  esos dos canales.
- **Nuevo acoplamiento Fase 1 → outbox de Fase 4:** el evento crítico #8 (alertas de seguridad) se origina en
  Paso 1.6/1.7, antes de que exista el worker de notificaciones (Fase 4). Recomendación: crear la tabla
  `outbox_events` desde Paso 1.2/1.7 (junto con `audit_logs`) aunque el worker que la consume no se construya
  hasta Fase 4 — los eventos de seguridad quedan encolados sin pérdida, simplemente no se entregan como
  notificación hasta que el worker exista. El control de seguridad real (revocar la sesión) ya es síncrono
  desde Paso 1.6 y no depende de esto.
- Plantillas de correo (Paso 4.2) se diseñan bajo la regla dura de "nunca información sensible completa,
  siempre enlace" desde el primer borrador, no como revisión posterior.
- "Documentos por archivar" queda confirmado como no crítico — desactivable por WhatsApp, sin cambio de
  esquema respecto al resto del catálogo de preferencias.

## Adenda — lo que se construyó en el Paso 4.2 (2026-08-25)

Alcance ejecutado: **`IN_APP` + `EMAIL`**, como manda este ADR. No se construyó
`device_tokens` ni nada de FCM, aunque el plan de trabajo lo pedía: ADR-0010 ya
había confirmado que no hay app móvil hasta después de Fase 5, y construir push
sin app que lo consuma es trabajo especulativo.

Tampoco existe la tabla `notification_preferences`. Según este mismo ADR, las
únicas preferencias posibles son de WhatsApp, y WhatsApp no está en el alcance:
la tabla no tendría ninguna fila que guardar. Se crea cuando se construya ese
canal. Los permisos `notifications.preferences.own` y `.company` ya existen en
el catálogo RBAC y quedan sin endpoint que los use hasta entonces.

**Decisiones tomadas al implementar:**

- `is_critical` se guarda en la fila de `notifications`, no se recalcula contra
  el catálogo al leer. Si mañana un evento cambia de categoría, lo ya enviado
  debe seguir contando la verdad de cuando se envió.
- Sin correo verificado la entrega queda `SKIPPED`, no `FAILED`. Enviar a una
  dirección sin verificar filtraría el aviso a quien haya puesto un correo
  ajeno al registrarse; y distinguirlo de un fallo real evita perseguir errores
  que no existen.
- `notification_deliveries` tiene índice único por `(notification_id, channel)`.
  Sin él, un reintento del worker crearía filas nuevas y el registro diría que
  se mandaron tres correos cuando se mandó uno.
- Las plantillas llevan versión (`PLANTILLA_VERSION`), guardada en cada entrega,
  para poder saber con qué texto salió un correo viejo después de rediseñarla.
- El correo se prueba contra Mailpit real, no contra un doble: lo que puede
  romperse es el diálogo SMTP y el armado del multipart, y un doble que acepta
  cualquier cosa no lo detecta.

**La regla de "ningún dato de negocio en el correo" es ahora una prueba.** Los
textos del catálogo son fijos y no admiten sustitución; hay un test que falla si
alguien introduce un marcador `{...}` en un asunto o cuerpo, y otro que verifica
contra el correo realmente recibido que no aparezca ningún identificador de
negocio ni adjunto. Sin esas pruebas, la regla se pierde en la primera mejora
bienintencionada de la plantilla.

## Enmienda — el identificador sí viaja en el correo (2026-08-25)

La regla original decía «ningún dato de negocio en el cuerpo del correo, solo
contexto mínimo + link». Al comparar contra el sistema viejo se vio que sus seis
correos sí llevaban datos —número de WR, shipper, carrier, lista de warehouses,
método de envío— y que el negocio los usaba: el cliente sabe de qué carga le
hablan sin entrar a nada.

**La regla queda así:**

| Sí viaja | No viaja |
|---|---|
| El identificador de la carga o del despacho: WR, número de factura, número de solicitud | Shipper, carrier, contenido, pesos, montos |
| El estado al que cambió | La lista completa de cargas de un despacho |
| El enlace al sistema | Cualquier documento adjunto |

**Por qué el identificador sí y el resto no.** El WR y el número de factura ya
están en los papeles del embarque y el cliente los tiene: repetirlos en el
correo no expone nada que no circule ya por su bandeja. El shipper, el
transportista y los pesos son otra cosa — son la relación comercial y la
operación, y de esos sí se aprende algo mirando un buzón ajeno.

Sigue prohibido: adjuntar documentos y mandar credenciales.

## Enmienda — las credenciales no viajan por correo (2026-08-25)

El sistema viejo mandaba usuario y contraseña en texto plano
(`templates/emails/credentials.html`). **No se replica.** Una contraseña enviada
por correo queda para siempre en el buzón de esa persona y en el de cualquiera a
quien se le reenvíe, y la conoce además quien creó la cuenta.

En su lugar, el alta manda un **enlace de invitación de un solo uso que vence a
las 48 horas**, donde la persona elige su propia contraseña. Usa el propósito
`INVITATION` de `one_time_tokens`, que ya existía.

Queda también la vía actual —el sistema genera una contraseña temporal y el
administrador la entrega por el medio que prefiera—, con el bloqueo por
`must_change_password` que obliga a cambiarla al primer acceso.
