# 02 — Arquitectura del estimador público de impuestos

> Estado: propuesta, 2026-10-12. No implementada. Depende de las decisiones de [07](07_DECISIONES_PENDIENTES.md).
> Las referencias `ruta:línea` salen de una auditoría del repositorio en `main` @ `5784aa8`, revisada después por Codex (ver [06](06_AUDITORIA.md)); las correcciones de esa revisión ya están aplicadas.

## 1. Auditoría del repositorio: lo que existe y condiciona el diseño

### 1.1 Amvi hoy

| Tema | Situación | Evidencia |
|---|---|---|
| Proveedor y modelo | SDK `openai` 3.19.2, `AsyncOpenAI`, Responses API. Modelo `gpt-5.6-luna` con `reasoning.effort="low"`, `store=False`, `max_output_tokens=1000`, timeout 30 s, 2 reintentos del SDK. `temperature` no se envía (los modelos de razonamiento la rechazan). | `backend/app/core/config.py:67-92`, `backend/app/modules/copilot/provider.py:181-218` |
| Circuit breaker | **Uno solo, global, en memoria**: 5 fallos lo abren 60 s | `provider.py:100-138` (`_breaker` en `:127`) |
| Bucle de herramientas | Hasta 6 llamadas al proveedor por turno, reenviando todo el historial en cada una | `copilot/service.py:297,314`, `config.py:91` |
| Streaming | No hay streaming de tokens: el texto llega en un solo evento SSE `token` | `copilot/service.py:308-310`, `copilot/router.py:163-164` |
| Herramientas | 8 de lectura (cargas, despachos, pendientes, `como_hago`) y 4 de escritura (borradores). Filtradas por permisos antes de ofrecerse y verificadas otra vez al ejecutar. | `copilot/tools.py:330-492` |
| Acoplamiento | `procesar_turno` usa los registros **globales** `HERRAMIENTAS`/`REGISTRO_EJECUTORES` y exige `PermisosEfectivos` y un `actor_user_id`. El historial exige `user_id NOT NULL`. | `copilot/service.py:50-53,77,143,199-205,238,347`; `copilot/models.py:117` |
| Límites de costo | Tope de 50k tokens por conversación en Redis (`config.py:98`), con clave `copilot:conversacion:{conversacion_id}`. **El id lo elige el cliente**: hoy el endpoint exige login, pero el mismo patrón en un endpoint público no frenaría a un anónimo. Se verifica antes y se contabiliza al final, así que no es un presupuesto duro. | `copilot/router.py:226-234,312-322` |
| Errores | El texto crudo del `OpenAIError` llega al navegador. El evento `fin` expone tokens y `response_id`. | `provider.py:221`, `service.py:301,368-375` |

### 1.2 Autenticación y superficie pública

- La autenticación es por dependencia de cada endpoint (`actor_actual`, `backend/app/modules/auth/dependencies.py:38-61`). No hay un middleware global (`backend/app/main.py:36-46`). **Un `APIRouter` sin `ActorDep` es público.**
- Endpoints públicos que ya existen: health, login/refresh/recuperación e invitaciones (`auth/router.py:74-487`). Patrón a seguir.
- Rate limiting: limitador propio con Redis, `backend/app/core/rate_limit.py:23-95`, que responde 429 con `Retry-After` (`core/errors.py:137-138`). `INCR` y `EXPIRE` van por separado (`rate_limit.py:49-52`): sirve para límites por ventana, **no** como presupuesto duro bajo concurrencia (ver §8).
- **La IP del cliente se puede falsificar.** Uvicorn corre con `--forwarded-allow-ips "*"` (`infra/produccion/docker-compose.yml:101-102`) y toma el primer valor de `X-Forwarded-For`. nginx no configura `real_ip`, y Cloudflare está delante. **Hay que corregirlo antes de cualquier límite por IP en un endpoint público** (el arreglo también protege el login actual).
- nginx: `/api/` sin `limit_req` ni `limit_conn`, `client_max_body_size 20m`, timeout de lectura de 120 s (`infra/produccion/nginx/produccion-app.amvarmar.com.conf:21-33`).

### 1.3 Frontend

- No hay `middleware.ts`. La guardia es `PortalProtegido`, del lado del cliente, en los layouts `(client)` y `(admin)` (`frontend/src/features/auth/portal-protegido.tsx:8-24`). El grupo `(auth)` es público: precedente para `(publico)`.
- **El layout raíz envuelve todo en `ProveedorSesion`**, que al montar llama a `refrescarAccessToken()` (`frontend/src/features/auth/contexto-sesion.tsx:56-60`). En una página pública, cada visitante anónimo dispararía un `POST /auth/refresh` (401 y gasto del límite de refresh). Ver la decisión D9 en 07.
- Reutilizables:
  - componentes `Boton`, `Campo`, `Modal`, `CargandoPagina`, `AvisoError` (`frontend/src/components/ui/*`);
  - tokens de diseño y modo oscuro en `frontend/src/app/globals.css:16-84`;
  - el lector SSE de `features/copilot/usar-chat.ts:100-206`.
- No reutilizable tal cual: `PanelChat` (`components/copilot/amvi-chat.tsx`) está atado a la sesión, el historial y las calificaciones.

### 1.4 Datos

- No hay código de moneda ni de tipo de cambio en el backend.
- Hay un patrón de `Decimal` con `ROUND_HALF_UP` en `backend/app/modules/shipments/peso.py:19-50`.
- `shipments.tariff_code` es texto de dígitos (CHECK `^[0-9]{1,40}$`). **No hay catálogo SAC.** En producción, **1 de 815 cargas** tiene partida: el LMS no aporta casos de prueba. Evidencia: consulta SQL de solo lectura a la base de producción, 2026-10-12, `count(tariff_code)` sobre `shipments` con `deleted_at IS NULL`.
- Extensiones creadas por las migraciones: pgcrypto, citext y **pg_trgm** (con índices GIN). La búsqueda de texto completo (`tsvector`, configuración `spanish`) viene incorporada en PostgreSQL 16, sin extensión. No están pgvector (no viene en `postgres:16-alpine`) ni unaccent. Que las extensiones estén activas en producción se confirma al desplegar, no desde el repo.
- Celery beat: `backend/app/workers/app.py:61-110`, con el patrón de tarea de `workers/tasks/copilot.py`.
- **Precedente explícito:** ADR-0012 quitó `cotizar_envio` porque no había una fuente determinista (`docs/adr/0012-asistente-virtual.md:206-211`). Este diseño respeta esa línea: **la IA no calcula**.

## 2. Principios

1. **El motor tributario es código determinista.** La IA no calcula ni elige tasas.
2. **La IA solo elige entre códigos que devuelve el backend.** Un código que no está en el catálogo no existe.
3. **Cada número lleva su procedencia:** fuente, fecha de verificación y versión de reglas.
4. **El módulo público está aislado del LMS:** no lee tablas de clientes, cargas, documentos ni usuarios, y no comparte herramientas, proveedor ni presupuesto con el Amvi del LMS.
5. **Sin infraestructura nueva:** mismo compose, misma base, mismo Redis, mismo Celery.

## 3. Componentes

```
                       (publico)/calculadora  ── Next.js, misma app
                                │  fetch sin credenciales
                                ▼
nginx (limit_req) ──► /api/v1/publico/estimador/*   ← router SIN ActorDep
                                │
          ┌─────────────────────┼──────────────────────┐
          ▼                     ▼                      ▼
   Clasificador            Motor tributario        Amvi público (fase 6)
   (IA + pg_trgm)          (Python + Decimal)      registro de herramientas propio:
   elige entre                  │                  buscar_incisos, calcular_estimacion
   candidatos                   │                  (sin herramientas del LMS)
          │                     │
          └──────────┬──────────┘
                     ▼
            Catálogo arancelario  ◄── Curaduría (admin LMS, autenticada)
            incisos, tasas con vigencia,      ▲
            fuentes, tipo de cambio           │
                     ▲                   Actualizador (Celery beat):
                     └────────────────── tipo de cambio diario; vigilancia
                                         de fuentes → avisa, no importa solo
```

| Componente | Responsabilidad | Ubicación propuesta |
|---|---|---|
| **Catálogo** | Incisos de 12 dígitos con descripción, jerarquía SAC, notas técnicas y exclusiones; tasas por tributo con vigencia y estado de verificación; fuentes | `backend/app/modules/aranceles/` (modelos, consultas) |
| **Motor** | Recibe datos normalizados y tasas; devuelve el desglose en cascada con base, tasa, monto y regla aplicada. Sin I/O. | `backend/app/modules/aranceles/motor.py` (funciones puras) |
| **Confianza** | Calcula el nivel (alto, medio, bajo, no estimable) con reglas verificables (ver 03) | `aranceles/confianza.py` |
| **Clasificador** | La IA extrae atributos y términos SAC; el backend busca candidatos con pg_trgm; la IA elige entre ellos y el backend valida | `aranceles/clasificador.py` |
| **Tipo de cambio** | Busca la venta del día: API Hacienda; si falla, BCCR SDDE con token. Guarda la serie. | `aranceles/tipo_cambio.py` + tarea beat |
| **API pública** | Endpoints sin autenticación, con límites | `aranceles/router_publico.py` |
| **Curaduría** | Pantalla de admin (LMS, autenticada) para verificar tasas contra TICA y marcar exclusiones | `aranceles/router_admin.py` + `frontend/src/app/(admin)/aranceles/` |
| **Evaluación** | Banco de casos reales anonimizados y script de métricas (ver 03) | `backend/scripts/evaluar_estimador.py` + tabla interna |
| **Página pública** | Formulario mobile-first; luego chat | `frontend/src/app/(publico)/calculadora/` |

Se llama `aranceles` y no `copilot` a propósito: el Amvi público es un **cliente** del módulo, no al revés.

## 4. Modelo de datos (propuesta)

Las tablas van en inglés, como el resto del esquema. Todas son nuevas y ninguna tiene FK a tablas del LMS, salvo los `users` que verifican tasas.

```
tariff_sources
  id uuid PK · name · url · organism · retrieved_at · file_sha256 · notes

tariff_lines                      -- un inciso EN UNA VERSIÓN de la nomenclatura
  id uuid PK
  code varchar(12)               -- inciso nacional, 12 dígitos
  hs_version varchar(10)         -- 'HS2022' (VII Enmienda); 'HS2028' después
  sac10 varchar(10)              -- inciso SAC regional
  description text               -- texto oficial
  chapter char(2) · heading char(4) · subheading char(6)
  valid_from date · valid_to date NULL
  source_id FK tariff_sources
  UNIQUE (code, hs_version)
  INDEX gin (description gin_trgm_ops); tsvector 'spanish' si mejora el recall
  -- correlaciones HS2022→HS2028 en una tabla aparte (pueden ser 1→N)

tariff_profiles                   -- perfil tributario COMPLETO de un inciso, publicado como unidad
  id uuid PK · line_id FK tariff_lines · version int
  status varchar(12)             -- DRAFT | PUBLISHED | SUSPENDED | SUPERSEDED
  legal_valid_from date · legal_valid_to date NULL   -- vigencia legal de las tasas
  observed_at date               -- cuándo se leyó en TICA
  review_due date                -- vencimiento de la revisión (D2)
  exclusion varchar(30) NULL     -- VEHICULO, ALCOHOL, TABACO, COMBUSTIBLE, …
  permits_status varchar(12)     -- REVIEWED_NONE | HAS_PERMITS | UNKNOWN (nunca se infiere de un JSON vacío)
  technical_notes jsonb          -- notas técnicas vigentes, con fuente
  published_by FK users · published_at · evidence text · change_reason text
  UNIQUE (line_id, version)

tariff_obligations                -- una fila por CADA tributo revisado del perfil
  profile_id FK tariff_profiles
  tax varchar(20)                -- DAI, SC, L6946, IVA, INDER, IFAM, FORESTAL, GE, …
  state varchar(16)              -- APPLIES | NOT_APPLICABLE | UNKNOWN | UNSUPPORTED
  kind varchar(12) NULL          -- PERCENT | PER_UNIT
  value numeric(18,6) NULL · unit varchar(20) NULL
  PK (profile_id, tax)
  -- el perfil solo se puede PUBLICAR si cada tributo de la lista oficial tiene un
  -- estado distinto de UNKNOWN; UNKNOWN o UNSUPPORTED bloquean el cálculo

exchange_rates
  rate_date date · source varchar(20) · usd_sell numeric(12,4) · usd_buy numeric(12,4)
  fetched_at · PK (rate_date, source)

tax_estimates                     -- cada estimación, reproducible
  id uuid PK · created_at
  share_token varchar(43) UNIQUE  -- token aleatorio (secrets.token_urlsafe) para compartir; revocable; distinto del id
  rules_version varchar(20)       -- versión del motor
  policies jsonb                  -- versiones de las políticas de redondeo, confianza y frescura usadas
  input jsonb                     -- datos normalizados (sin datos personales)
  profile_id FK tariff_profiles NULL · line_code varchar(12) NULL · hs_version
  profile_snapshot jsonb          -- copia del perfil y sus obligaciones tal como se usaron
  fx_snapshot jsonb               -- fecha efectiva, fecha de consulta, fuente, serie y valor
  result jsonb                    -- desglose
  confidence varchar(12) · confidence_reasons jsonb
  classification_id FK NULL       -- clasificación guardada en el servidor (ver §6)
  client_hash varchar(64)         -- seudónimo: HMAC de la IP con sal rotativa (no es anonimato)
  retention: purgar a los 180 días desde la creación

tax_eval_cases                    -- interno; nunca expuesto públicamente
  id · category · description · inputs jsonb · expected_line_code
  expected_taxes jsonb · expected_total_crc · fx_used · source (DUA/especialista)
  anonymized bool · created_by · created_at
```

**Reproducibilidad.** Hay dos promesas distintas:
- **Mostrar el resultado archivado.** Siempre posible durante la retención, porque `tax_estimates` guarda **copias** del perfil, las obligaciones, el tipo de cambio y las políticas usadas.
- **Recalcularlo.** Exige el mismo motor. `REGLAS_VERSION` cambia con cada cambio de fórmula; los tests dorados de cada versión quedan en el repo, y el git tag de la versión permite ejecutarla otra vez.

Después de la purga a 180 días no se promete ninguna de las dos.

**Publicación.** Una corrección de curaduría crea una **versión nueva del perfil completo** y la publica como unidad. Nunca hay combinaciones de tasas viejas y nuevas. Cada publicación guarda autor, motivo, evidencia y el antes y después, y se puede revertir publicando la versión anterior. Un cambio normativo conocido **suspende** de inmediato los perfiles afectados, aunque se hayan revisado ayer.

Para la regla anti-solapamiento de vigencias entre perfiles publicados de un mismo inciso se usa `EXCLUDE USING gist (line_id WITH =, daterange(legal_valid_from, legal_valid_to, '[)') WITH &&) WHERE (status = 'PUBLISHED')`. Requiere la extensión `btree_gist`.

## 5. Flujo de datos

**MVP (formulario):**
1. El usuario escribe el producto y llena cantidad, precio unitario, moneda, flete, seguro y país de origen.
2. `POST /clasificar`:
   - la IA extrae atributos y propone **términos de búsqueda en vocabulario SAC**;
   - el backend busca los mejores candidatos con pg_trgm sobre `tariff_lines.description`;
   - la IA elige entre esos candidatos (o pide un dato), con justificación;
   - el backend **valida** que el código esté entre los candidatos y en el catálogo.
3. Se muestran el inciso propuesto y las alternativas. El usuario confirma o cambia.
4. `POST /calcular` (sin IA):
   - toma el **perfil publicado** vigente del inciso y el tipo de cambio del día;
   - corre el motor y calcula la confianza;
   - guarda `tax_estimates` y devuelve el desglose.
5. La página muestra cada tributo con base, tasa y monto, más los supuestos, las fuentes, la fecha de verificación, el nivel de confianza y los avisos. **Aparte**, y rotulados como "no son impuestos", van los honorarios, el almacenaje y el flete local.

**Fase 6 (Amvi conversacional):** el mismo flujo, pero Amvi conduce la conversación y llama a `buscar_incisos` y `calcular_estimacion`. **El número que muestra Amvi es el que devolvió el motor**, renderizado por la UI desde el resultado estructurado y no desde el texto libre del modelo.

## 6. Endpoints

Todos son públicos, bajo `/api/v1/publico/estimador`, con límites (sección 8):

| Método y ruta | IA | Descripción |
|---|---|---|
| `GET /incisos?q=` | No | Búsqueda en el catálogo (pg_trgm), para quien conoce su código o quiere buscar a mano |
| `POST /clasificar` | Sí | `{descripcion ≤500, pais_origen?, respuestas?: {clasificacion_id, atributos}}` → `{clasificacion_id, candidatos[], elegido?, preguntas[], justificacion}`. La clasificación (candidatos recuperados, atributos confirmados, versiones de catálogo, prompt y modelo) **se guarda en el servidor**. Las respuestas a las preguntas vuelven con el mismo `clasificacion_id`. |
| `POST /calcular` | No | `{clasificacion_id + inciso_elegido | inciso_manual, cantidad, precio_unitario_usd, flete_total_usd?, seguro_total_usd?, pais_origen?, regimen: "DEFINITIVA"}` → `{estimacion, share_token, desglose, total o rango, confianza, motivos, supuestos, fuentes, avisos}`. **El servidor** decide si había alternativas, si la categoría está habilitada y qué nivel corresponde; el cliente no puede declararlo. MVP: solo USD; flete y seguro son totales del envío. Decimales como cadenas canónicas, validando signo, precisión y máximos. |
| `GET /estimaciones/compartidas/{share_token}` | No | Proyección **pública** de una estimación: sin hash de cliente, ids internos ni evidencia de curaduría. Revocable. |
| `GET /tipo-cambio` | No | Tipo de cambio usado hoy y su fuente |

Admin, autenticados con `ActorDep` y un permiso nuevo `tariffs.manage`, bajo `/api/v1/aranceles`:
- curaduría de tasas: verificar, corregir, excluir;
- carga de fuentes;
- cola de "verificaciones vencidas";
- banco de casos de evaluación y reporte de métricas.

Fase 6: `POST /api/v1/publico/amvi/respond` (SSE), con **su propio** proveedor, breaker, registro de herramientas y límites.

## 7. Integración con Amvi

- **No se reutiliza `procesar_turno`.** Hace falta un turno público con su propio registro de herramientas. Lo que exige `procesar_turno` (permisos, actor, herramientas globales) es lo que no queremos en el lado público.
- Lo que se reutiliza de verdad es **menos de lo que parece** (corrección de la auditoría, 06):
  - `ProveedorOpenAI` **no** se puede "instanciar aparte": su constructor toma clave, modelo y límites de `get_settings()` y `responder()` usa el `_breaker` global (`copilot/provider.py:177-198`). El módulo necesita **su propio adaptador** de OpenAI con configuración, cliente, credenciales y breaker inyectados. No se debe alternar la configuración global entre solicitudes.
  - El protocolo `ProveedorIA` exige además OCR (`provider.py:85`). El adaptador público implementa una interfaz más chica, sin OCR.
  - `DefinicionHerramienta` exige una regla RBAC (`tools.py:317`) y `esquemas_openai()` rechaza modelos anidados (`esquemas_openai.py:100-107`). Se reutiliza el **patrón**, con un registro propio de dos herramientas.
  - Del frontend se extrae el **parser SSE** (`usar-chat.ts:100-111`) a una utilidad neutral; el hook usa `fetchAutenticado` y no sirve tal cual.
  - El fixture `usar_proveedor` solo sustituye la fábrica actual: el módulo necesita su propia fábrica de pruebas.
  - Una prueba abre el breaker público y comprueba que el Amvi del LMS sigue funcionando, y al revés.
- **El Amvi del LMS puede ganar una herramienta `estimar_impuestos`** que llame al mismo motor. Es opcional, para una fase posterior, y no cambia nada de lo actual.
- **Prompt del Amvi público:** solo estimación de impuestos; nunca afirma una tasa ni un código que no venga de una herramienta; ante la duda, dice "no estimable" y deriva a un especialista; no habla de clientes ni de cargas.

## 8. Seguridad del módulo público

| Riesgo | Control |
|---|---|
| IP falsificable | nginx valida la IP con `real_ip_header CF-Connecting-IP` + `set_real_ip_from` (rangos de Cloudflare, IPv4 e IPv6) y la manda en una cabecera canónica **sobrescrita**; uvicorn confía solo en el par real nginx→contenedor. Pruebas: cabeceras falsificadas, IPv6 y acceso directo al origen. Además, verificar la configuración **activa** en la VPS, no solo la del repo. **Requisito previo.** |
| Abuso o consumo de tokens | Límites por IP con `core/rate_limit.py` para búsquedas y cálculos. El **presupuesto de IA** se implementa aparte, con una operación atómica (script Lua en Redis): se **reserva** el costo máximo de la llamada antes de hacerla y se concilia con el uso real después, incluidos reintentos, cancelaciones y errores. Si Redis no responde, la IA se apaga. También hay un tope de llamadas concurrentes. Proyecto de OpenAI aparte con **límite duro** de gasto mensual (el límite de OpenAI tiene demora de aplicación; el de Redis es el que corta). Al agotarse el presupuesto, queda el buscador manual. |
| Bots | Cloudflare Turnstile (plan gratuito: desafíos ilimitados, verificado en https://developers.cloudflare.com/turnstile/plans/). El backend **valida** el token, el hostname y la acción (los tokens duran 5 minutos y son de un solo uso) y emite una autorización anónima de corta duración que limita las llamadas siguientes. Pruebas: token ausente, reutilizado, vencido y de otro sitio. Ver D6. |
| DoS | nginx `limit_req` y `limit_conn` en `/api/v1/publico/`; cuerpo máximo de 16 KB; timeouts cortos; sin adjuntos; sin SSE en el MVP. |
| Prompt injection | La IA solo tiene herramientas de **lectura del catálogo**; su salida se valida (código ∈ candidatos); el texto del usuario va como dato; el prompt no lleva secretos; la UI muestra números del motor, no texto del modelo. |
| Acceso al LMS | Router separado y registro de herramientas propio, **y además** una conexión a la base con un **rol SQL de mínimos privilegios** (solo lectura de catálogo y tipo de cambio, escritura en `tax_estimates`/clasificaciones), sin permisos sobre tablas del LMS. La curaduría usa otra conexión. Pruebas: el rol público no puede leer `shipments`, `users`, etc.; el registro público no contiene herramientas del LMS. Riesgo residual que se acepta: mismo proceso y misma máquina (memoria, credenciales del proceso y disponibilidad compartidas). |
| Exposición de errores | Mensajes genéricos; nunca el error del proveedor, los tokens ni el `response_id`. No repetir el patrón actual de `provider.py:221` (que convierte la excepción en texto SSE antes de que actúe cualquier manejador HTTP). Pruebas específicas de errores saneados. |
| Contenido del modelo | Los permisos, supuestos y fuentes se renderizan desde **datos estructurados**. Los enlaces solo pueden ir a fuentes de una lista aprobada. Pruebas con contenido adversarial, también en descripciones importadas del catálogo. |
| Datos personales | No se piden, pero la descripción libre puede traerlos. Se minimiza lo que se manda al proveedor y lo que se loguea; las descripciones se purgan a los 180 días; la IP solo como HMAC con sal rotativa (seudonimización, no anonimato). |
| Interferencia con el LMS | Breaker propio: una caída por tráfico público no apaga al Amvi de los clientes. Con presupuesto propio, el gasto público no consume el del LMS. |

## 9. Actualización de datos

| Dato | Frecuencia | Cómo |
|---|---|---|
| Tipo de cambio | Diario (tarea beat) | API de Hacienda `/indicadores/tc`; si falla, BCCR SDDE. Si no hay tipo del día, se usa el último con aviso y **baja la confianza**. |
| Perfiles por inciso | Por vencimiento de revisión | Estados únicos (D2): `PUBLISHED` con revisión vigente; `PUBLISHED` con revisión vencida (confianza media como máximo) hasta el límite duro; después `SUSPENDED` (no estimable). Un cambio normativo conocido pasa a `SUSPENDED` de inmediato. |
| Fuentes (SIECA, COMEX, La Gaceta) | Semanal | Una tarea revisa `dateModified` o los hashes de las páginas conocidas y abre un **incidente** con responsable y plazo: cambio detectado → incisos afectados → suspensión si procede → revisión → publicación → re-evaluación. No importa nada sola. |
| Tratados | Cada 1 de enero | Recalcular los calendarios de desgravación (fase 7) |
| HS 2028 | Antes del 1-ene-2028 | Migrar el catálogo con las correlaciones SIECA y versionar `hs_version` |

## 10. Manejo de errores

- Sin catálogo para el inciso: respuesta "no estimable" con su motivo, no un error 500.
- Tipo de cambio desactualizado: se calcula, con aviso, y la confianza baja.
- Proveedor de IA caído o presupuesto agotado: la clasificación responde "no disponible, buscá tu inciso" y el cálculo sigue funcionando.
- **No se mantiene una transacción ni una sesión de base abierta mientras responde la IA.**
- Límite excedido: 429 con `Retry-After` y un mensaje claro.

## 11. Pruebas

- **Motor:** casos dorados del instructivo DGA (01 §8), recalculados al céntimo; propiedades (monotonía, cascada, `Decimal` sin `float`); un test por tributo.
- **Confianza:** tabla de casos → nivel esperado, un test por cada regla.
- **Clasificador:** proveedor falso (`tests/integration/conftest.py` `usar_proveedor`) que intenta devolver un código fuera de los candidatos. Debe rechazarse.
- **API pública:** sin token funciona; con un token del LMS no gana nada; límites que devuelven 429; registro público sin herramientas del LMS.
- **Aislamiento y presupuesto:**
  - agotamiento del presupuesto con llamadas concurrentes (no se puede pasar del tope);
  - caída de Redis (la IA se apaga);
  - independencia de los breakers (público ↔ LMS);
  - cancelaciones y reintentos contabilizados;
  - el rol SQL público no lee tablas del LMS;
  - cadena de proxy real (Cloudflare → nginx → uvicorn) en un entorno controlado.
- **Clasificador:** además de códigos inexistentes, prompt injection que intenta forzar **otro código válido**, quitar atributos o alterar valores.
- **Evaluación:** el script de 03 sobre el banco de casos. No es un test de CI, es un reporte.

## 12. Despliegue

- Mismo `docker-compose`, sin servicios nuevos. Migraciones Alembic para las tablas nuevas. Tareas nuevas en `workers/app.py`.
- nginx: zona `limit_req` para `/api/v1/publico/` y el arreglo de `real_ip`.
- Variables nuevas: `ESTIMADOR_OPENAI_API_KEY` (o reutilizar la clave con un proyecto aparte), `BCCR_SDDE_TOKEN`, `TURNSTILE_SECRET`, límites y presupuesto diario.
- **Interruptor dinámico:** un flag en Redis, auditado y consultado por el backend en cada solicitud, con alcance separado para clasificación con IA, cálculo, chat y enlaces compartidos. Una variable de entorno **no** sirve: `get_settings()` está cacheado con `lru_cache` (`backend/app/core/config.py:169-171`) y requiere reiniciar. Apagar la página no apaga la API: el flag apaga los endpoints.
