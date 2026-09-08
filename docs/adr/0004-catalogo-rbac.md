# ADR-0004: Catálogo de permisos RBAC y roles iniciales

- **Fecha:** 2026-08-19
- **Estado:** Aprobado
- **Aprobado por:** Otoniel González (revisión técnica) + AMVARMAR (decisión de negocio)

## Contexto

Roles de punto de partida propuestos: `SUPER_ADMIN`, `OPS_ADMIN`, `OPS_AGENT`, `CLIENT_ADMIN`, `CLIENT_USER`.
Falta la matriz completa rol × permiso × scope (`GLOBAL` o `COMPANY`).

## Decisión

**Roles:** `SUPER_ADMIN`, `OPS_ADMIN`, `OPS_AGENT`, `CLIENT_ADMIN`, `CLIENT_USER`.

**Catálogo de alcances (`scope_type`) — extiende el modelo original del documento de arquitectura:**

| Alcance | Descripción |
|---|---|
| `GLOBAL` | Acceso a todas las empresas, cargas y operaciones de AMVARMAR. |
| `ORGANIZATION` | Acceso únicamente a la información de la empresa cliente del usuario. Reemplaza el nombre `COMPANY` del documento de arquitectura original — mismo concepto, mismo campo `scope_company_id`. |
| `ASSIGNED` | Acceso únicamente a cargas asignadas al colaborador. **Nuevo, no existía en el diseño original.** |
| `OWN` | Acceso únicamente a registros creados por el usuario. **Nuevo, no existía en el diseño original.** Definido en el catálogo pero sin uso activo en la matriz actual — queda disponible para permisos futuros. |

**Catálogo de permisos y matriz rol × alcance:**

| Código de permiso | Funcionalidad | SUPER_ADMIN | OPS_ADMIN | OPS_AGENT | CLIENT_ADMIN | CLIENT_USER |
|---|---|---|---|---|---|---|
| `shipments.read` | Ver cargas (alcance determina si son todas o solo de su empresa) | GLOBAL | GLOBAL | GLOBAL/ASSIGNED | ORGANIZATION | ORGANIZATION |
| `shipments.create` | Crear prealertas | Sí | Sí | Sí | Sí | Sí |
| `shipments.update` | Editar datos de una prealerta | Sí | Sí | Sí | Sí | Sí |
| `shipments.transition.forward` | Cambiar estados operativos, incluida entrega (`→DELIVERED`) | Sí | Sí | Sí | No | No |
| `dispatch_requests.create` | Solicitar despacho | Sí | Sí | Sí | Sí | Sí |
| `dispatch_requests.approve` | Aprobar una solicitud de despacho | Sí | Sí | Sí | No | No |
| `dispatch_requests.reject` | Rechazar una solicitud (motivo obligatorio) | Sí | Sí | Sí | No | No |
| `dispatch_requests.prepare` | Preparar despacho | Sí | Sí | Sí | No | No |
| `dispatch_requests.complete` | Confirmar/finalizar despacho | Sí | Sí | Sí | No | No |
| `shipments.transition.backward` | Corregir estados hacia atrás | Sí | Sí | No | No | No |
| `shipments.transition.revert_delivered` | Revertir `DELIVERED` | Sí | No | No | No | No |
| `shipments.cancel.prealert` | Cancelar una carga en `PRE_ALERT` | Sí | Sí | Sí | Sí | No |
| `shipments.cancel.in_transit` | Cancelar una carga en `IN_TRANSIT` | Sí | Sí | No | No | No |
| `shipments.reopen` | Reabrir una carga cancelada | Sí | Sí | No | No | No |
| `documents.upload.client` | Subir documentos del cliente | Sí | Sí | Sí | Sí | Sí |
| `documents.upload.internal` | Subir packing list y BL | Sí | Sí | Sí | No | No |
| `documents.verify` | Verificar o rechazar documentos | Sí | Sí | Sí | No | No |
| `documents.invalidate` | Eliminar o invalidar documentos | Sí | Sí | No | No | No |
| `shipments.legacy_review.resolve` | Resolver revisiones legacy (`legacy_review_required`) | Sí | Sí | No | No | No |
| `shipments.legal_hold.manage` | Activar/desactivar `legal_hold` (retención especial, ADR-0007) | Sí | Sí | No | No | No |
| `shipments.requirement.manage` | Abrir, cancelar y resolver requisitos de una carga (Paso 3.4) | Sí | Sí | Sí | No | No |
| `shipments.requirement.waive` | **Exonerar** un requisito obligatorio: deja avanzar SIN el documento | Sí | Sí | No | No | No |
| `system_settings.manage` | Configurar límites de archivo y otros ajustes de sistema (ADR-0009) | Sí | No | No | No | No |
| `companies.manage` | Gestionar empresas cliente | Sí | Sí | No | No | No |
| `users.create.internal` | Crear usuarios internos (staff) | Sí | Sí | No | No | No |
| `users.manage` | Gestionar usuarios de una empresa | Sí (GLOBAL) | Sí (GLOBAL) | No | Sí (ORGANIZATION) | No |
| `rbac.manage` | Gestionar roles y permisos | Sí | No | No | No | No |
| `audit_logs.read` | Consultar auditoría | Sí (GLOBAL) | Sí (GLOBAL) | No | Sí (ORGANIZATION) | No |
| `reports.export` | Exportar reportes | Sí | Sí | Según asignación (ASSIGNED) | Sí | No |
| `notifications.preferences.own` | Configurar notificaciones propias | Sí | Sí | Sí | Sí | Sí |
| `notifications.preferences.company` | Configurar notificaciones de la empresa | Sí | Sí | No | Sí | No |

Nota de implementación: "Ver todas las cargas" y "Ver cargas de su empresa" del documento original son **un
solo permiso** (`shipments.read`) — la diferencia la da el `scope_type` de la asignación (`GLOBAL` vs
`ORGANIZATION`), no dos permisos separados. Mismo patrón para `audit_logs.read` y `users.manage`.

**Revisión al construir el Paso 1.4** — se cerraron dos huecos del catálogo original:

1. **`dispatch_requests.approve` y `.reject` no existían.** El Paso 3.3 define esos endpoints y ADR-0008 los
   trata como evento crítico de notificación, pero ningún permiso los cubría. Se agregan con el mismo alcance
   que `prepare`/`complete` (incluye `OPS_AGENT`): el agente operativo ya prepara, despacha y verifica
   documentos, así que aprobar no le da poder que no tuviera. Además `prepare`/`.complete`, que figuraban como
   una sola fila, se separan en dos códigos — son dos endpoints distintos.
2. **`shipments.cancel` se divide en `shipments.cancel.prealert` y `shipments.cancel.in_transit`.** La matriz
   original codificaba la restricción de estado dentro de la celda (`OPS_AGENT` solo desde `PRE_ALERT`), lo
   que no es expresable con un solo permiso: obligaría al motor de transiciones a consultar el rol del actor,
   acoplando la lógica de dominio a nombres de rol concretos. Con dos códigos, la tabla
   `shipment_status_transitions` (Paso 2.1) — que ya guarda qué permiso exige cada transición — resuelve el
   caso sin lógica adicional, y agregar un rol nuevo no obliga a tocar código.

**Dónde vive el alcance (`scope_type`):** en `user_role_assignments`, no en `roles`. El documento de
arquitectura lo pone en `roles`, pero eso no soporta que un `OPS_AGENT` sea `GLOBAL` o `ASSIGNED` según el
puesto de cada persona, que es lo que este mismo ADR exige. `roles.allowed_scopes` declara qué alcances admite
un rol (validación), y cada asignación fija el alcance concreto.

**Responsabilidades por rol:**

- **`SUPER_ADMIN`** — administrador técnico y de seguridad. Alcance `GLOBAL`. Gestiona roles/permisos/admins.
  Único que revierte `DELIVERED`. Consulta toda la auditoría. No se usa para operación cotidiana.
  **Requiere autenticación multifactor** — ver "Consecuencias".
- **`OPS_ADMIN`** — administrador de operaciones. Alcance `GLOBAL`. Gestiona cargas, clientes, usuarios internos,
  documentos. Corrige estados hacia atrás con justificación. Resuelve revisiones legacy. No modifica permisos
  ni revierte `DELIVERED`.
- **`OPS_AGENT`** — colaborador operativo. Alcance `GLOBAL` o `ASSIGNED` según el puesto (se define por
  asignación individual, no por el rol en sí). Registra recepción/almacenamiento/preparación/despacho/entrega.
  Carga y verifica documentos. Puede cancelar desde `PRE_ALERT`. No administra usuarios, no modifica permisos,
  no corrige estados hacia atrás, no elimina documentos (lo solicita a un admin).
- **`CLIENT_ADMIN`** — administrador de empresa cliente. Alcance `ORGANIZATION`. Ve todas las cargas/documentos
  de su empresa. Crea prealertas, solicita despachos, carga documentos. Cancela cargas de su empresa en
  `PRE_ALERT`. Gestiona usuarios de su empresa. No modifica estados operativos ni verifica documentos.
- **`CLIENT_USER`** — usuario regular de empresa cliente. Alcance `ORGANIZATION`. Consulta cargas de su empresa,
  crea prealertas, solicita despachos, sube documentos. No gestiona usuarios, no cancela, no modifica estados.

**Reglas de seguridad:**
- Deny-by-default: sin permiso explícito, sin acceso. Ya es el diseño de Paso 1.4.
- El backend valida siempre permiso + alcance; ocultar botones en frontend no es control de seguridad
  (ya es la regla explícita de Fase F en el plan).
- Aislamiento de empresa: un cliente nunca consulta datos de otra empresa, ni con UUID válido (`404`, no `403`
  — ya definido en Paso 1.4/2.2).
- Nadie elimina registros de `audit_logs` — sin `DELETE` a nivel de permiso de aplicación **y** sin `GRANT DELETE`
  a nivel de rol de base de datos para el usuario de la aplicación (defensa en profundidad).
- Acciones sensibles requieren motivo obligatorio y quedan auditadas (ya cubierto para transiciones en ADR-0001).
- Cuentas no comparten usuario/contraseña — regla operativa, no aplicable por esquema; se refuerza limitando
  sesiones concurrentes visibles en `GET /auth/sessions` para que el titular note actividad ajena.
- Permisos ampliables sin tocar roles base — ya es el diseño (`role_permissions` separado de `roles`), consistente.

## Alternativas consideradas

- Usar un solo scope `COMPANY`/`GLOBAL` sin agregar `ASSIGNED`/`OWN`, y resolver "cargas asignadas" con lógica
  de aplicación ad-hoc en vez de un scope formal. Descartada: mezclar reglas de alcance dentro de la lógica de
  negocio en vez del motor de RBAC rompe el principio de Paso 1.4 (permisos efectivos calculables de forma
  centralizada) y sería más difícil de auditar.

## Consecuencias

- `user_role_assignments.scope_type` crece de 2 a 4 valores: `GLOBAL`, `ORGANIZATION`, `ASSIGNED`, `OWN`.
  Paso 1.4 debe sembrar el enum ampliado desde el inicio, no como migración posterior.
- **`ASSIGNED` requiere una relación nueva que no está en el modelo de datos actual:** qué `OPS_AGENT` está
  asignado a qué `shipment`. Hace falta una tabla (ej. `shipment_assignments(shipment_id, user_id)`) o un campo
  de asignación en `shipments`. Esto se agrega al alcance de Paso 2.2, no estaba contemplado en el documento
  de arquitectura original.
- **`OWN` requiere columnas `created_by_user_id`** en las tablas donde se use en el futuro. Ninguna de las
  filas de la matriz actual lo usa, así que no bloquea Fase 1/2, pero si se activa después hay que agregar
  la columna donde corresponda (probable candidato: documentos subidos por `CLIENT_USER`).
- **MFA para `SUPER_ADMIN` es un requisito nuevo**, no estaba en el documento de arquitectura ni en el plan de
  Fase 1 original (Paso 1.6/1.8 solo cubren Argon2id + JWT). Se agrega a Paso 1.8: enrolamiento TOTP, tabla de
  secretos MFA, verificación de segundo factor en `/auth/login` cuando el usuario tiene rol `SUPER_ADMIN`.
  Esto amplía el alcance de Fase 1 respecto a como estaba escrito en el plan original.
- `documents.invalidate` ("eliminar o invalidar documentos") se implementa como invalidación lógica
  (marca de estado, el archivo permanece en storage), no como `DELETE` físico — **confirmado por ADR-0007**:
  ningún documento se elimina físicamente nunca, ni siquiera por retención (se recomprime, no se borra). Este
  permiso invalida/oculta el documento del uso normal, pero el archivo sigue existiendo igual que cualquier
  otro caso de la aplicación.
- 31 códigos de permiso quedan listos para `scripts/seed_rbac.py` (Paso 1.4) tal como están en la tabla de
  arriba — el seed puede escribirse directamente desde este ADR sin traducción adicional.

## Adenda — Paso 3.4: requisitos documentales

Hasta el Paso 3.4 los endpoints de requisitos (`POST` y `PATCH
/shipments/{id}/requirements`) no verificaban permiso ni empresa: bastaba estar
autenticado. Cualquier usuario podía abrir o exonerar un requisito obligatorio
de una carga de otra empresa. Se cierran con los dos permisos de arriba y con
la comprobación de alcance contra `shipments.company_id`.

`waive` es permiso propio y no lo tiene `OPS_AGENT` porque exonerar no es
resolver: deja despachar una carga **sin** el documento que el catálogo exige.
Verificar y rechazar siguen siendo trabajo del agente.
