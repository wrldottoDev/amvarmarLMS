# ADR-0010: Aplicación móvil

- **Fecha:** 2026-08-19
- **Estado:** Aprobado
- **Aprobado por:** Otoniel González (revisión técnica) + AMVARMAR (decisión de negocio)

## Contexto

El legacy ya tiene `ClientDevice` con tokens FCM (Android/iOS/Web), lo que sugiere que hubo o hay planes de app
móvil. Esta decisión determina si `device_tokens` y push notifications son prioridad de la Fase 4 o se pueden
posponer sin costo.

## Decisión

**Sí, se desarrollan apps nativas — Android y iOS separadas — pero solo en la etapa final**, después de que la
plataforma web esté terminada, probada y estable en producción.

**Stack:** Android en Kotlin (preferible Jetpack Compose), iOS en Swift (preferible SwiftUI). Clientes nativos
de la misma API — no reimplementan reglas de negocio, no tienen base de datos propia, consumen `/api/v1` igual
que Next.js.

**Orden de desarrollo (nueva fase al final del plan, después de Fase 5):**

```
Backend + base de datos (Fase 1-2) → Plataforma web completa (Fase F) → Estabilización en producción
  (Fase 5, Paso 5.7) → App Android → App iOS
```

**Criterios de arranque de la fase móvil** (todos deben cumplirse, no es una fecha fija):
- Todas las funciones de la web están aprobadas y en producción.
- API estable y documentada (`/openapi.json` completo, sin endpoints en borrador).
- Flujos operativos probados con usuarios reales — no solo QA interno.
- Sin errores críticos pendientes (0 abiertos en el rastreador, no "pocos").
- **Contratos de API versionados** — implica una disciplina que no era necesaria antes: una vez que las apps
  móviles dependen de `/api/v1`, un cambio incompatible ya no se puede hacer in-place sobre esa ruta (una app
  en producción no se puede forzar a actualizar tan rápido como un despliegue web). Cambios incompatibles a
  partir de este punto requieren `/api/v2`, coexistiendo con `/v1` mientras existan apps viejas en uso. Antes
  de esta fase, romper `/v1` en desarrollo era aceptable; después, no.

**Alcance funcional:** consultar cargas, crear prealertas, solicitar despachos, gestionar documentos, recibir
notificaciones. Mismos roles/permisos/alcances que la web — sin excepción ni atajo para móvil.

**Notificaciones push — confirma y cierra el punto que había quedado abierto en ADR-0008:** `device_tokens`,
Firebase Cloud Messaging (Android) y Apple Push Notification Service (iOS) se posponen **hasta esta fase**, no
antes. No es parte de Fase 4. La tabla `device_tokens` ya existe en el diseño original del documento de
arquitectura (sección 3.8) — se activa recién aquí, no se construye antes por especulación.

**Reglas de los tokens de dispositivo:**
- Vinculados a usuario + dispositivo, revocables individualmente.
- Almacenados de forma segura (cifrados, como ya definía ADR-0004/arquitectura para tokens FCM).
- No sustituyen el correo obligatorio (ADR-0008) — push es adicional, nunca el único canal para algo crítico.
- Respetan el mismo catálogo de eventos críticos de ADR-0008 (push crítico llega igual que WhatsApp cuando el
  canal está habilitado, mismas reglas de no poder apagarse individualmente).

**Seguridad específica de cliente móvil:**
- Tokens de autenticación (JWT/refresh) en Android Keystore / iOS Keychain — nunca en `SharedPreferences`/
  `UserDefaults` sin cifrar.
- Nunca se guarda contraseña en el dispositivo.
- Toda autorización se valida en FastAPI — el cliente móvil no decide nada, igual que la regla ya establecida
  para el frontend web (Fase F).
- Aislamiento por empresa aplica igual que en web (`404` cross-company, no `403`).
- Cerrar sesión revoca la sesión del backend **y** el `device_token` asociado — logout desde móvil debe llamar
  al mismo endpoint `/auth/logout` (Paso 1.8) y adicionalmente desactivar el token push de ese dispositivo.

**Mientras tanto:** Next.js (Fase F) debe ser responsive — funcionar correctamente en teléfonos y tablets desde
el día en que se construye, no como ajuste posterior. Es el único acceso móvil hasta que exista la app nativa,
potencialmente por años.

## Alternativas consideradas

- App multiplataforma (React Native / Flutter) para una sola base de código. Descartada explícitamente por la
  decisión a favor de nativos separados (Kotlin/Compose, Swift/SwiftUI) — más control sobre Keystore/Keychain
  y sobre las APIs de notificaciones push de cada plataforma.
- Construir `device_tokens`/FCM/APNs en paralelo con la web (Fase 4), para no repetir trabajo después.
  Descartada: es esfuerzo especulativo sin app que lo consuma — ya estaba anotado como riesgo en ADR-0008.

## Consecuencias

- **Nueva fase en el plan de trabajo**, después de Fase 5 (Paso 5.7): "Fase M — Aplicaciones móviles", con los
  5 criterios de arranque de arriba como gate de entrada. No estaba en el documento de arquitectura original
  ni en el plan tal como se escribió — se agrega ahora que la decisión de negocio ya está tomada.
  Estructura: M.1 setup + auth Android, M.2 funcionalidad completa Android, M.3 publicación Play Store,
  M.4 setup + auth iOS, M.5 funcionalidad completa iOS, M.6 publicación App Store, M.7 push (FCM + APNs,
  `device_tokens` activo).
- **Disciplina de versionado de API se vuelve obligatoria a partir de esta fase** — antes de que exista una
  app en producción, romper `/v1` en desarrollo temprano es aceptable; después no. Vale la pena adoptar la
  disciplina de versionado desde antes (Fase 1) para no tener que aprenderla bajo presión cuando ya haya apps
  dependientes.
- ADR-0008 queda cerrado en su punto abierto sobre `PUSH`: confirmado que se construye, confirmado que es en
  la fase móvil, no en Fase 4.
- Fase F gana un requisito no funcional explícito desde el diseño: responsive real en móvil/tablet, porque
  será el único acceso móvil durante toda la Fase 1-5.
- `requirements.txt`/`pyproject.toml` del backend ya incluyen `firebase-admin` — queda instalado pero sin
  activar (sin credenciales de producción configuradas) hasta que arranque la Fase M. APNs (iOS) requiere
  librería adicional no incluida todavía (ej. `aioapns` o integración vía FCM unificado para ambas plataformas
  — a decidir en Fase M, no ahora).
