# 06 — Auditoría técnica independiente (Codex)

> Estado: auditoría **ejecutada de verdad**, 2026-10-12. No es una simulación.
> - **Herramienta:** `codex-cli 0.159.2` (`codex exec`), modelo `gpt-6-astra`, razonamiento `high`, **sandbox de solo lectura** sobre el repositorio en `main` @ `5784aa8` más los documentos 01-05 y 07.
> - **Alcance pedido:** los 8 ejes del brief: arquitectura, seguridad pública, integración con Amvi, motor tributario, clasificación, pruebas, escalabilidad y mantenimiento. Separando hallazgos con evidencia de opiniones, verificando las referencias `ruta:línea` y sin inventar normativa.
> - **El informe original está íntegro en el anexo.** Solo se cambiaron las rutas absolutas locales por rutas relativas al repo.

## 1. Cómo se trató la auditoría

No se aceptó nada automáticamente. Cada hallazgo se contrastó con el código o con los documentos:
- **Aceptado:** la evidencia lo confirma; el documento afectado ya está corregido.
- **Aceptado con matiz:** se confirma, pero se ajusta el alcance o la solución.
- **Rechazado:** la evidencia no lo sostiene.

Verificaciones propias hechas al contrastar (todas confirmaron lo que dijo Codex):
- `ProveedorOpenAI.__init__` lee `get_settings()` y `responder()` usa el `_breaker` global (`backend/app/modules/copilot/provider.py:177-198`).
- `INCR` y `EXPIRE` van en operaciones separadas (`backend/app/core/rate_limit.py:49-52`).
- `get_settings()` está cacheado con `@lru_cache` (`backend/app/core/config.py:169-171`).
- `user_id` de `copilot_conversations` no admite nulos (`backend/app/modules/copilot/models.py:117`).
- `ProveedorIA` exige `describir_factura` (`provider.py:85`) y `DefinicionHerramienta` exige `autorizacion: ReglaAutz` (`tools.py:317`).

## 2. Resumen del auditor

La propuesta es una buena base para un prototipo interno, pero **todavía no sustenta una apertura pública con confianza "alta"**. Los bloqueos principales que señala:
- aislamiento incompleto respecto de Amvi;
- control de gasto bajo concurrencia;
- contratos de API insuficientes para aplicar las reglas de confianza;
- versionado del catálogo, que necesita cubrir perfiles completos, permisos y vigencias, no solo tasas sueltas.

**Coincidimos.** Las correcciones de diseño están aplicadas en 02, 03, 04, 05 y 07.

## 3. Referencias de 02

Codex verificó 26 referencias: 23 correctas y 3 incorrectas. Las 3 incorrectas están corregidas:

| Referencia | Problema | Corrección aplicada |
|---|---|---|
| Evidencia de `user_id NOT NULL` | Se citaba `service.py`; la restricción está en el modelo | Ahora cita `copilot/models.py:117` |
| "`ProveedorOpenAI` instanciado aparte, con su propio breaker" | Falso: el constructor usa la configuración global y `responder()` el breaker global | 02 §7 ahora exige un **adaptador propio** con configuración y breaker inyectados |
| "`tsvector` no está" | Error mío: `tsvector` es un tipo incorporado de PostgreSQL 16, no una extensión | 02 §1.4 corregido: el texto completo está disponible; faltan pgvector y unaccent |

## 4. Hallazgos y veredicto

| # | Sev. | Hallazgo (resumen) | Veredicto | Qué cambió |
|---|---|---|---|---|
| 1 | Alta | IP manipulable: uvicorn `*` + Cloudflare conserva el X-Forwarded-For del cliente | **Aceptado** (coincide con 02 §1.2) | 02 §8: cabecera canónica sobrescrita, confianza solo en nginx→contenedor, pruebas con cabeceras falsas e IPv6, verificar la configuración activa |
| 2 | Alta | Instanciar otro `ProveedorOpenAI` no aísla (configuración y breaker globales) | **Aceptado** (verificado) | 02 §7 y 04 F4: adaptador propio con configuración y breaker inyectados; prueba de independencia de breakers |
| 3 | Alta | El contador de Redis no es un presupuesto duro bajo concurrencia | **Aceptado** (verificado) | 02 §8: reserva atómica (Lua) antes de cada llamada, conciliación, tope de concurrencia, IA apagada si cae Redis; límite duro del proyecto de OpenAI |
| 4 | Alta | Router y herramientas separados no aíslan la base del LMS | **Aceptado** | 02 §8: rol SQL de mínimos privilegios + prueba; riesgo residual del mismo proceso declarado; D21 |
| 5 | Alta | `/calcular` no recibe lo que necesitan las reglas de confianza | **Aceptado** | 02 §6: `clasificacion_id` guardado en el servidor, régimen declarado, y el nivel lo decide el servidor |
| 6 | Alta | Verificar tasas sueltas no prueba que estén todos los tributos | **Aceptado** | 02 §4: `tariff_profiles` + `tariff_obligations` con estados `APPLIES / NOT_APPLICABLE / UNKNOWN / UNSUPPORTED`, publicados como unidad |
| 7 | Alta | No se versionan ni verifican el catálogo ni los permisos | **Aceptado** | 02 §4: `tariff_lines` por versión HS, `permits_status` explícito (nunca inferido de un JSON vacío), notas técnicas en el perfil verificado |
| 8 | Alta | Política de frescura contradictoria; "garantía tributaria" excesiva | **Aceptado** | 02 §9 y D2: una sola tabla de estados; suspensión inmediata por cambio normativo. 03 §1: "se reduce el riesgo, no se garantiza" |
| 9 | Alta | El versionado no conserva la semántica histórica | **Aceptado con matiz** | 02 §4: versiones de catálogo y perfiles, snapshot de perfil y políticas, distinción entre "mostrar archivado" y "recalcular" (git tag por versión). Se declara que no hay reproducibilidad después de la purga. Matiz: no se conserva un "artefacto ejecutable" aparte; alcanza con el tag del repo y los tests dorados por versión. |
| 10 | Alta | Que el código esté entre los candidatos no evita clasificar mal ni la manipulación semántica | **Aceptado** | 03 §2.1: recall@K antes de la selección. 02 §11: pruebas de inyección que fuerzan **otro código válido**. 03 §4: el intervalo no cubre lo que la búsqueda no trajo. Las notas legales de capítulo quedan para F4. |
| 11 | Alta | 20-30 casos con cero errores no sostienen "alta" (límite superior de 9,5% y 13,9%) | **Aceptado** (cálculo correcto: regla del tres) | 03 §3.2: piloto para fijar umbrales + conjunto reservado dimensionado con unos 3/*p* casos de nivel alto; D11 |
| 12 | Alta | Comparar el NMF de hoy contra DUAs históricos engaña | **Aceptado** | 03 §2.4: reproducción histórica (perfil y tipo de cambio de la fecha del DUA) separada de la estimación actual; casos fuera de alcance etiquetados |
| 13 | Alta | El lanzamiento manual permitía saltarse la evaluación; D11 circular con F0 | **Aceptado** | 04 F0 y F5 + D13: el lanzamiento sin IA también exige F1-F3 y los controles; D11 se decide con el piloto |
| 14 | Media | "Al céntimo", ±¢1 y redondeo supuesto son contratos distintos | **Aceptado** | 03 §1 y §2.3: igualdad exacta contra una política de redondeo versionada (a verificar) y ±¢1 solo contra DUAs; casos de medias unidades y varias líneas |
| 15 | Media | Faltan contratos monetarios (moneda, flete y seguro unitarios o totales) | **Aceptado** | 02 §6: MVP solo en USD; flete y seguro son totales; decimales como cadenas validadas |
| 16 | Media | Semántica inconsistente de rangos y del seguro omitido | **Aceptado** | 03 §4: tres formas de resultado (total, intervalo entre clasificaciones, piso incompleto); flete y seguro tratados igual |
| 17 | Media | Turnstile sin validación de servidor definida | **Aceptado** | 02 §8: validación de token, hostname y acción; autorización anónima corta; pruebas de token ausente, reutilizado y vencido |
| 18 | Media | Las descripciones pueden traer datos personales; el enlace con UUID es una capacidad de acceso | **Aceptado** | 02 §4 y §6: `share_token` aleatorio y revocable, proyección pública sin campos internos, HMAC de IP declarado como seudonimización |
| 19 | Media | La reutilización real de Amvi es menor que la lista de símbolos | **Aceptado** (verificado) | 02 §7 reescrito con lo que se reutiliza de verdad |
| 20 | Media | "Carga trivial" sin capacidad demostrada; almacenamiento subestimado | **Aceptado** | 05 §4: condicionado a medir carga pico y SLO del LMS; almacenamiento ×2-3 por índices, WAL y respaldos; 02 §10: sin transacciones abiertas durante la IA |
| 21 | Media | La caché puede guardar clasificaciones obsoletas | **Aceptado** | 05 §6.3: clave con atributos y versiones, invalidación al publicar, LRU acotada |
| 22 | Media | La vigilancia de fuentes no cierra el incidente; ATENA y HS 2028 inferidos | **Aceptado** | 02 §9: flujo de incidente con responsable y plazo (D23); 01 marca la continuidad de TICA en 2027 como inferencia **a verificar** |
| 23 | Media | El interruptor "sin redeploy" no existe con `lru_cache` | **Aceptado** (verificado) | 02 §12: flag dinámico en Redis por función; D22 |
| 24 | Media | Cifras "comprobadas" sin evidencia reproducible (815 cargas, 41% de disco) | **Aceptado con matiz** | Las cifras salen de comandos que corrí en esta sesión (consulta SQL de solo lectura a producción y salida de `vigilar.sh`), pero los documentos no lo decían: ahora citan método y fecha. "No existe API" se cambió por "no se localizó". La gratuidad del SDDE quedó coherente: INFERIDA. |
| 25 | Baja | `tsvector` mal descrito; `EXCLUDE` como pseudocódigo | **Aceptado** | 02 §1.4 corregido; 02 §4 con `EXCLUDE USING gist (…)` concreto y `btree_gist` |

**Omisiones señaladas por Codex (todas aceptadas):**
- **Pruebas de concurrencia, caída de Redis, breakers, cancelaciones, rol SQL y cadena de proxy real.** Incorporadas en 02 §11 y en los criterios de F5 (04).
- **Adjudicación independiente de etiquetas, división por familias y conjunto reservado.** En 03 §3.3.
- **Error absoluto, política cuando el real es cero, colas y calibración más allá de la mediana.** En 03 §2.4 y §2.5.
- **Tipo de cambio: fecha efectiva frente a fecha de consulta, discrepancias entre Hacienda y el BCCR, validación de la serie.** En 02 §4 (`fx_snapshot`) y 04 F2 (alerta en vez de elegir sola). La equivalencia normativa sigue **a verificar**.
- **Validación del texto y los enlaces del LLM.** En 02 §8 ("Contenido del modelo").
- **Trazabilidad y reversión de la curaduría.** En 02 §4 ("Publicación").

**Ningún hallazgo se rechazó.** Los dos matices (9 y 24) ajustan el alcance; no niegan el problema.

## 5. Lo que el auditor considera bien fundamentado

- El motor determinista separado de la IA, y Amvi como consumidor, en línea con ADR-0012.
- No reutilizar `procesar_turno`.
- Los snapshots de tasas y tipo de cambio, como base de la trazabilidad.
- La validación de códigos en el backend y la abstención, sin confundirlas con exactitud de clasificación.
- El formulario antes que el chat, sin adjuntos, con NMF y exclusiones explícitas.
- La separación de métricas, sin porcentajes inventados.
- El ejemplo aritmético de 01 §8: Codex lo recalculó y da **¢1.730.870,40**.
- Los precios de `gpt-5.6-luna` y `gpt-5.6-terra`, con respaldo oficial.
- La curaduría humana reconocida como dependencia central.

## 6. Qué queda abierto después de la auditoría

La normativa que los documentos marcan **a verificar** sigue así: seguro presunto, base del SC con la Ley 6946, redondeo, régimen courier y montos vigentes de específicos y cargos. La auditoría **no** la reemplaza por interpretaciones; se resuelve en F0 con un especialista o con la DGA (D15).

---

## Anexo — Informe original de Codex

## Resumen

Revisé los seis documentos y el código pertinente en `5784aa820ab9bc554997b9f0c756912969c655ac`, el commit citado por 02.  
La auditoría fue de solo lectura; no modifiqué archivos ni ejecuté pruebas contra producción.  
**Opinión del auditor:** la propuesta es una buena base para un prototipo interno, pero todavía no sustenta una apertura pública con confianza “alta”.  
Los principales bloqueos son el aislamiento incompleto de Amvi, el control de gasto concurrente y contratos insuficientes para aplicar las reglas tributarias y de confianza.  
El catálogo necesita versionar también incisos, permisos y completitud; verificar tasas individualmente no garantiza una liquidación completa.  
La evaluación separa correctamente sus capas, pero sus muestras y criterios de habilitación necesitan mayor rigor.  
No identifiqué una vulnerabilidad crítica demostrada del estimador —aún no existe—; sí riesgos altos del diseño y problemas comprobables en componentes que propone reutilizar.

## Verificación de referencias de 02

En esta sección, `copilot/` abrevia `backend/app/modules/copilot/`; `auth/`, `backend/app/modules/auth/`. “Correcta” significa que el código respalda la afirmación, no que el comportamiento sea recomendable ni que se haya comprobado el despliegue activo.

| Referencia | Correcta/incorrecta | Nota |
|---|---|---|
| `backend/app/core/config.py:67-92`; `copilot/provider.py:181-218` | Correcta | Modelo, razonamiento, timeout, salida máxima y `store=False` coinciden. Los dos reintentos provienen del SDK, no de estas líneas. La instalación local contiene OpenAI 3.19.2; `requirements.txt:66` permite versiones posteriores menores que 4. |
| `copilot/provider.py:100-138`, especialmente `:127` | Correcta | Breaker global **por proceso**, compartido entre instancias. Defaults de cinco fallos y 60 segundos en configuración. |
| `copilot/service.py:297,314`; `config.py:91` | Correcta | El bucle permite seis llamadas a `responder` y acumula el contexto. No constituye un máximo absoluto de llamadas externas: los adjuntos añaden OCR en `service.py:259`; algunas herramientas también llaman al proveedor. |
| `copilot/service.py:308-310`; `copilot/router.py:163-164` | Correcta | El texto de cada respuesta final se emite completo en un evento; el formateador SSE, por sí solo, no demuestra esa granularidad. |
| `copilot/tools.py:330-492` | Correcta | Ocho herramientas de lectura y cuatro de escritura/propuesta. La segunda comprobación se invoca efectivamente desde `service.py:105`. |
| `copilot/service.py:50-53,77,143,238,347` | Correcta | Demuestra registros globales y acoplamiento. La firma con actor y permisos está en `:199-205`. |
| Las mismas líneas como evidencia de `user_id NOT NULL` | Incorrecta | Esa restricción no está en `service.py`. La evidencia correspondiente es `copilot/models.py:117`: `Mapped[UUID]` no nullable. |
| `copilot/router.py:226-234` | Correcta | El identificador viene del cliente y determina la clave. El máximo 50.000 está en `config.py:98`. **El endpoint actual exige autenticación**; el comentario sobre anónimos describe el riesgo de reutilización. |
| `copilot/provider.py:221`; `service.py:301,368-375` | Correcta | Se propaga el texto de la excepción y se exponen contadores y `response_id`. |
| `auth/dependencies.py:38-61`; `backend/app/main.py:36-46` | Correcta | Autenticación por dependencia. Para confirmar la ausencia de autenticación global también hay que leer `core/middleware.py:65-66`, que solo instala `RequestIdMiddleware`. |
| `auth/router.py:74-487` | Correcta | Contiene los endpoints públicos enumerados. No todo ese rango es público: incluye logout, sesiones y solicitud de verificación autenticados. Health está en otro módulo. |
| `backend/app/core/rate_limit.py:23-95` | Correcta | Contadores Redis y excepción 429. `Retry-After` se agrega en `core/errors.py:137-138`. **INCR y EXPIRE no forman una operación atómica conjunta.** |
| `infra/produccion/docker-compose.yml:101-102` | Correcta | Confianza en `*`. El Uvicorn instalado selecciona el primer XFF cuando se configura así. No prueba por sí solo la configuración activa de producción. |
| `infra/produccion/nginx/produccion-app.amvarmar.com.conf:21-33` | Correcta | Cuerpo de 20 MB, lectura de 120 segundos y ausencia de límites locales. El archivo conserva XFF mediante `$proxy_add_x_forwarded_for`. |
| `frontend/src/features/auth/portal-protegido.tsx:8-24` | Correcta | Guardia cliente. Ambos layouts protegidos la utilizan; no encontré `middleware.ts` ni `proxy.ts` en el frontend revisado. |
| `frontend/src/features/auth/contexto-sesion.tsx:56-60` | Correcta | Intenta refresh si no hay access token. La envoltura global se confirma en `app/layout.tsx:28` y `components/proveedores.tsx:14-18`. |
| `frontend/src/components/ui/*`; `app/globals.css:16-84` | Correcta | Existen los componentes, variables de diseño y estilos oscuros indicados. |
| `frontend/src/features/copilot/usar-chat.ts:100-206` | Correcta | Contiene el parser y consumo SSE. Solo son reutilizables directamente las partes independientes: el hook usa `fetchAutenticado` en `:150`. |
| `frontend/src/components/copilot/amvi-chat.tsx` | Correcta | `PanelChat` utiliza sesión, historial y calificaciones; ver `:60-90`. |
| `backend/app/modules/shipments/peso.py:19-50` | Correcta | Usa `Decimal` y `ROUND_HALF_UP`, **a tres decimales para peso**. No demuestra una regla monetaria o aduanera. |
| `backend/app/workers/app.py:61-110`; `workers/tasks/copilot.py` | Correcta | Hay beat y tareas reutilizables como patrón. La retención actual es de 180 días **sin uso**, no necesariamente desde creación. |
| `docs/adr/0012-asistente-virtual.md:206-211` | Correcta | Retira `cotizar_envio` por falta de fuente determinista. |
| `copilot/provider.py:80-87,169`, asociado a “instanciados aparte, con su propio breaker” | Incorrecta | Las clases existen, pero otra instancia conserva el breaker global y la configuración del LMS. Hace falta modificar el contrato o implementar un adaptador independiente. |
| `copilot/esquemas_openai.py:141`; `copilot/tools.py:313` | Correcta | Los símbolos existen. La reutilización exige considerar RBAC obligatorio en `DefinicionHerramienta` y restricciones del generador de esquemas. |
| `tests/integration/conftest.py`, `usar_proveedor` | Correcta | Existe bajo `backend/tests/`. Solo sustituye la fábrica del router de Amvi actual; no sustituirá automáticamente una fábrica pública independiente. |
| 02 §1.4: extensiones y `tsvector` | Incorrecta | Las migraciones crean `pgcrypto`, `citext` y `pg_trgm`; no acreditan su estado actual en producción. `tsvector` es un tipo incorporado en PostgreSQL 16, no una extensión ausente. [Documentación oficial](https://www.postgresql.org/docs/16/datatype-textsearch.html). |

## Hallazgos

Las **evidencias** siguientes son constataciones del código o los documentos. Las consecuencias y recomendaciones son el juicio técnico del auditor. Para abreviar, `01`, `02`, etc. identifican los documentos de `docs/estimador-impuestos/`.

1. **Alta · Ejes 2 y 7 — La identidad por IP es manipulable en la configuración versionada.**

   **Evidencia:** [docker-compose.yml:101](infra/produccion/docker-compose.yml:101), nginx `:28` y `auth/router.py:70-89`. Cloudflare conserva una cadena XFF preexistente; Uvicorn con `*` toma su primer elemento. Una comprobación local, sin tráfico de red, confirmó ese comportamiento. [Cloudflare documenta cómo conserva y amplía XFF](https://developers.cloudflare.com/fundamentals/reference/http-headers/).

   **Recomendación:** convertir la IP validada por nginx en una cabecera canónica sobrescrita, confiar únicamente en los pares reales nginx→Docker y mantener los rangos Cloudflare. Probar IPv4/IPv6, cabeceras falsificadas y acceso directo al origen. Verificar la configuración activa: los archivos del repo no prueban qué está cargado en la VPS.

2. **Alta · Ejes 1 y 3 — Instanciar otro proveedor no produce el aislamiento prometido.**

   **Evidencia:** 02:193 frente a [provider.py:177](backend/app/modules/copilot/provider.py:177) y `:196`. El constructor no recibe parámetros: usa `openai_api_key`, `copilot_model` y límites globales; `responder()` obtiene `_breaker` global.

   **Recomendación:** explicitar en F4 un proveedor con configuración, cliente, credenciales y breaker inyectados por módulo. Añadir una prueba que abra el breaker público y compruebe que Amvi sigue funcionando, y viceversa. No modificar `get_settings()` global para alternar claves entre solicitudes.

3. **Alta · Ejes 2 y 7 — El contador reutilizable no garantiza un presupuesto duro bajo concurrencia.**

   **Evidencia:** [rate_limit.py:49](backend/app/core/rate_limit.py:49) ejecuta `INCR` y después `EXPIRE`; una interrupción intermedia deja una clave sin vencimiento. `restantes()` y `sumar()` son operaciones separadas. Amvi verifica antes y contabiliza después, únicamente al recibir `fin` (`copilot/router.py:230-231,312-322`); una salida temprana por error puede omitir consumo previo.

   **Recomendación:** reservar capacidad atómicamente antes de cada llamada, reconciliar consumo y conservar reservas ante resultados inciertos. Contabilizar llamadas parciales, cancelaciones y reintentos; limitar concurrencia y definir comportamiento si Redis falla. Un presupuesto monetario debe distinguir entrada y salida. Configurar explícitamente el límite duro del proyecto: una alerta no corta tráfico y aun el límite duro tiene demora de aplicación. [OpenAI: límites de gasto](https://developers.openai.com/api/docs/guides/spend-limits).

4. **Alta · Ejes 1, 2 y 3 — Router y herramientas separados no son una frontera completa de aislamiento del LMS.**

   **Evidencia:** 02:53-54,208 promete aislamiento conservando infraestructura. [docker-compose.yml:29](infra/produccion/docker-compose.yml:29) configura una conexión de base compartida; `core/database.py:61-85` ofrece un engine y un sessionmaker comunes. No se propone un rol SQL público restringido. El test “no contiene herramientas del LMS” solo cubre el registro.

   **Recomendación:** usar una conexión con permisos mínimos para catálogo y estimaciones, sin acceso a tablas del LMS; separar la conexión de curaduría. Probar los permisos reales de la base y los límites de recursos. Documentar que permanecer en el mismo proceso conserva riesgos compartidos de memoria, credenciales y disponibilidad.

5. **Alta · Ejes 1, 4 y 5 — El contrato de `/calcular` no permite aplicar varias reglas de confianza.**

   **Evidencia:** [02:175](docs/estimador-impuestos/02_ARQUITECTURA.md:175) define inciso, cantidad, precio, moneda, flete, seguro y origen. No recibe régimen, exención, referencia a clasificación, alternativas ni confirmación. Sin embargo, 03:119,128,137,144 exige decidir con esos datos. `/clasificar` devuelve preguntas, pero no define cómo continuar con las respuestas.

   **Recomendación:** diseñar un contexto de estimación validado por el servidor, con clasificación identificada/versionada, atributos confirmados y alcance declarado. Mantener el ingreso manual con su confianza limitada. El cliente no debe poder declarar por su cuenta “sin alternativas” o “categoría habilitada”.

6. **Alta · Ejes 4 y 8 — Verificar todas las filas existentes no demuestra que estén todos los tributos necesarios.**

   **Evidencia:** 03:162 afirma que una omisión impide verificar el inciso, pero [02:114](docs/estimador-impuestos/02_ARQUITECTURA.md:114) solo modela estados de tasas individuales. No distingue explícitamente tributo no aplicable, tasa cero, dato desconocido y fila faltante. Tampoco hay una aprobación del conjunto completo del inciso.

   **Recomendación:** publicar versiones completas del perfil tributario del inciso, con lista de obligaciones revisadas y estados explícitos. Bloquear el cálculo ante obligaciones desconocidas o no soportadas. Publicar las correcciones como una unidad para evitar combinaciones transitorias de tasas viejas y nuevas.

7. **Alta · Ejes 4, 5 y 8 — Se controla la antigüedad de tasas, pero no suficientemente la del catálogo y los permisos.**

   **Evidencia:** 01:59-60 acepta la estructura de 2022 como base; 04:65-66 importa incisos/descripciones y cruza DAI regional. Las notas técnicas de `tariff_lines` no tienen vigencia ni verificación propias (02:103-111). A la vez, 01:214 reconoce cambios de control en 2026 y 03 considera crítico omitir un permiso.

   **Recomendación:** curar también existencia y vigencia nacional del código, descripción, exclusiones y requisitos. Registrar revisión explícita de “sin permisos”, no interpretarla desde un JSON vacío. El cruce regional de DAI no acredita por sí mismo el desdoble nacional ni los requisitos costarricenses.

8. **Alta · Ejes 4 y 8 — La política de frescura es contradictoria y se presenta como una garantía tributaria excesiva.**

   **Evidencia:** 02:218 convierte una tasa vencida en “sin verificar”; 03:120 bloquea tasas sin verificar; D2 propone revisión a 90 días y límite duro a 180. No queda definido el comportamiento entre ambos. Además, 03:12 garantiza la capa tributaria mientras la revisión esté vigente.

   **Recomendación:** separar vigencia legal, fecha de observación y vencimiento de revisión. Definir una única tabla de estados y transiciones. Un cambio normativo conocido debe invalidar inmediatamente el dato afectado, aunque haya sido revisado ayer. La curaduría reciente reduce riesgo; no garantiza ausencia de cambios posteriores.

9. **Alta · Ejes 1, 4 y 8 — La estrategia de versionado no alcanza para conservar la semántica histórica.**

   **Evidencia:** [02:103](docs/estimador-impuestos/02_ARQUITECTURA.md:103) usa `code` como PK y `hs_version` como atributo: no permite dos versiones del mismo código con distintas descripciones o notas. Los snapshots guardan tasas y cambio, pero no especifican versiones de catálogo, notas, exclusiones, confianza ni redondeo. Guardar un SHA identifica código; no define cómo ejecutarlo posteriormente.

   **Recomendación:** versionar entidades arancelarias y publicaciones completas, preservando relaciones de correlación y vigencias. Guardar las políticas necesarias para reconstruir el resultado y conservar el artefacto ejecutable correspondiente. Diferenciar “mostrar el resultado archivado” de “recalcularlo”. Fijar también qué reproducibilidad se promete después de la purga a 180 días.

10. **Alta · Eje 5 — La pertenencia a candidatos evita códigos inventados, pero no clasificaciones equivocadas ni manipulación semántica.**

    **Evidencia:** 02:156-159 busca sobre `description`; 05:85 propone descripciones recortadas. El modelo no incluye explícitamente notas legales de sección/capítulo ni reglas interpretativas. 04:113 reconoce bajo recall, pero 03 no incluye formalmente el recall del conjunto recuperado antes de seleccionar.

    **Recomendación:** medir recuperación y selección por separado, incluyendo recall@K previo al LLM. Incorporar contexto jerárquico, notas relevantes, atributos obligatorios y abstención. Un único candidato recuperado no demuestra unicidad técnica. Probar prompt injection que fuerce **otro código válido**, reduzca atributos o altere valores; rechazar solo códigos inexistentes no detectaría esos casos. El mínimo/máximo entre candidatos tampoco cubre códigos que la búsqueda omitió.

11. **Alta · Eje 6 — La muestra propuesta no sustenta el significado operativo de confianza “alta”.**

    **Evidencia:** 03:93 propone 20–30 casos por categoría; 03:109 habilita alta con cero errores críticos; D11 fija umbrales después de medir. No se fija un mínimo de casos **de nivel alto** por categoría. Con cero errores en 30 observaciones independientes, el límite superior binomial unilateral del 95% es aproximadamente **9,5%**; en 20, **13,9%**.

    **Recomendación:** usar el piloto para decidir umbrales y congelarlos antes de una evaluación nueva e independiente. Dimensionar la muestra según el error crítico tolerable y reportar el número efectivo por categoría/nivel. La regla `±1/√n` presupone condiciones de muestreo que un banco curado y agrupado por productos puede incumplir.

12. **Alta · Ejes 4 y 6 — Comparar NMF de hoy contra DUAs históricos puede producir métricas engañosas.**

    **Evidencia:** 02:162 selecciona tasas y cambio del día; 03:40 compara el flujo completo con lo pagado; 01:192 establece NMF en el MVP. Un DUA puede corresponder a otra fecha, preferencias, exenciones o cargos por declaración. El protocolo no define cómo conciliar esas diferencias.

    **Recomendación:** separar reproducción histórica con condiciones idénticas, evaluación de clasificación y estimación pública actual. Etiquetar y excluir o normalizar casos fuera de alcance. Documentar diferencias de valoración, reparto de gastos y cargos. Comparar “tasas equivalentes” debe incluir también bases, G/E, Ley 6946 y cargos aplicables: la lista de 03:25 no basta.

13. **Alta · Ejes 1 y 6 — La alternativa de lanzamiento manual permite saltar controles que deberían ser obligatorios.**

    **Evidencia:** [04:135](docs/estimador-impuestos/04_PLAN_IMPLEMENTACION.md:135) permite F5 desde F2, omitiendo F3; `confianza.py` completa aparece en F4. Esto contradice “medición antes de exposición” y deja sin especificar el control de confianza del lanzamiento manual. F0 exige resolver todas las decisiones, pero D11 debe decidirse después de F3/F4.

    **Recomendación:** distinguir dependencias de IA y dependencias de seguridad/confiabilidad. Todo lanzamiento, también manual, debe exigir motor, curaduría, política de confianza, evaluación pertinente y controles públicos. Reescribir las decisiones por fase para eliminar la dependencia circular.

14. **Media · Ejes 4 y 6 — “Al céntimo”, tolerancia de ±¢1 y redondeo supuesto son contratos distintos.**

    **Evidencia:** 03:11 promete exactitud al céntimo; 03:36 y 04:54 aceptan ±¢1; 01:244 reconoce que la regla de redondeo no está verificada. 04:49 adopta `ROUND_HALF_UP` configurable. Tampoco fija si redondea cada base, tributo, línea o únicamente el total.

    **Recomendación:** cerrar y versionar una política de redondeo con especialista/DUAs; mientras tanto, marcarla **a verificar**. Exigir igualdad exacta contra esa política en pruebas matemáticas y tratar aparte diferencias contra liquidaciones externas. Agregar casos de medias unidades, varias líneas y cascadas. La ausencia de `float` no demuestra corrección tributaria.

15. **Media · Ejes 1 y 4 — Faltan contratos monetarios y de valoración indispensables.**

    **Evidencia:** 02:177 admite `moneda`, pero el modelo de cambio solo guarda USD/CRC (02:126-128). No define moneda de flete/seguro, si estos importes son totales o unitarios, ni si el precio ya los incluye. El motor admite cargos opcionales, pero el endpoint no expresa todos sus determinantes.

    **Recomendación:** limitar inicialmente las monedas y modalidades soportadas; definir importes, unidades y alcance de valoración. Transportar decimales como cadenas canónicas, validar finitud, signo, precisión y máximos desde la API hasta los snapshots. Rechazar casos no representables. La aplicabilidad normativa de ajustes y cargos queda **a verificar**, no debe resolverse suponiendo cero.

16. **Media · Ejes 4 y 6 — Los rangos y la omisión del seguro tienen semántica inconsistente.**

    **Evidencia:** 03:109-110 y `:171` anuncian rangos para confianza media; `:133` define un total. Para flete desconocido, `:127-129` exige mínimo–máximo pero solo identifica un piso. Para seguro desconocido, `:134` calcula sin él y asigna media.

    **Recomendación:** distinguir intervalo entre clasificaciones, escenario con supuestos y piso incompleto. Sin una cota respaldada para flete o seguro, no presentar un máximo ni cobertura estadística. Aplicar un tratamiento coherente a ambos componentes desconocidos y explicar qué parte del total falta.

17. **Media · Ejes 2 y 3 — “Turnstile en la primera visita” no define un control verificable por el backend.**

    **Evidencia:** 02:205 y D6 no especifican validación servidor, prueba de visita, expiración ni protección de llamadas siguientes. Turnstile exige validación servidor; sus tokens duran cinco minutos y son de un solo uso. [Documentación oficial](https://developers.cloudflare.com/turnstile/get-started/server-side-validation/).

    **Recomendación:** validar token, hostname y acción; emitir una autorización anónima limitada y expirable, o validar por operación según la política elegida. Probar token ausente, reutilizado, vencido y de otro sitio. Extender la política al chat y establecer cuotas globales para búsquedas y cálculos que siguen disponibles sin IA.

18. **Media · Ejes 2 y 8 — No pedir datos personales no garantiza que las descripciones no los contengan.**

    **Evidencia:** 02:133,140,178,210 combina entrada libre, retención de 180 días, hash de IP y lectura pública mediante UUID. No define una proyección pública distinta de la fila interna, revocación del enlace ni tratamiento de texto identificable. “UUID” tampoco especifica una variante aleatoria.

    **Recomendación:** tratar el enlace como una capacidad de acceso, con token aleatorio y expiración/revocación definidas. Publicar únicamente campos aprobados, sin hashes de cliente, identificadores internos ni evidencia privada de curaduría. Definir minimización, logs, respaldos y texto enviado al proveedor. Un hash estable de IP es seudonimización técnica, no prueba de anonimato.

19. **Media · Ejes 1 y 3 — La reutilización real de Amvi es menor que la enumeración de símbolos.**

    **Evidencia:** `ProveedorIA` exige OCR (`provider.py:85`); `DefinicionHerramienta` exige `ReglaAutz` (`tools.py:317`); `esquemas_openai.py:100-107` rechaza modelos anidados. El hook SSE usa autenticación y refresh. El fixture solo modifica la fábrica actual.

    **Recomendación:** reutilizar DTOs y utilidades neutrales mediante interfaces pequeñas. Extraer el parser SSE y el transporte de IA configurable; crear orquestación, herramientas, fábrica de pruebas y cliente HTTP públicos. Mantener la validación Pydantic al ejecutar. Añadir pruebas de errores saneados: los manejadores HTTP genéricos no corrigen una excepción ya convertida en texto SSE.

20. **Media · Ejes 7 y 8 — La afirmación de carga “trivial” se basa en promedios, sin capacidad demostrada.**

    **Evidencia:** 05:60 usa 100.000 consultas/mes ≈ 2–3/minuto. El cálculo es correcto, pero no considera ráfagas, concurrencia ni latencia externa. El compose comparte Redis, PostgreSQL y workers; `workers/app.py:18-19` usa Redis también como broker/backend. Los 3 GB de 05:61 cuentan esencialmente payload, no índices, WAL, respaldos o espacio de mantenimiento.

    **Recomendación:** definir carga pico y SLO del LMS; medir ambos sistemas simultáneamente. Establecer límites de concurrencia, tiempos de consulta y capacidad reservada. No mantener transacciones abiertas mientras responde el LLM. Planificar purgas por lotes, memoria de caché y aislamiento de tareas lentas. El costo incremental cero debe quedar condicionado a esas mediciones.

21. **Media · Ejes 5 y 8 — La caché propuesta puede conservar clasificaciones obsoletas o mezclar contextos.**

    **Evidencia:** 05:83 propone clave por descripción normalizada durante 30 días. El resultado depende también de atributos aclarados, catálogo, prompt, modelo y restricciones vigentes.

    **Recomendación:** incluir esas versiones y entradas en la clave; invalidar al publicar cambios; no guardar cálculos con tasas o cambio actuales como si fueran clasificaciones estables. Acotar cardinalidad y memoria: descripciones arbitrarias permiten generar muchas claves aunque casi no existan aciertos.

22. **Media · Eje 8 — La vigilancia de fuentes no define cómo cerrar el incidente de actualización.**

    **Evidencia:** 02:219 solo observa `dateModified` o hashes y avisa. 04:177-181 menciona alertas, HS 2028 y ATENA sin procedimiento de publicación, cuarentena o contingencia. El calendario de ATENA en 01:45 es inferido; la continuidad de todas las importaciones en TICA durante 2027 no se demuestra a partir de una primera salida de exportaciones.

    **Recomendación:** asignar responsable, suplente, plazo de respuesta y catálogo de fuentes/disposiciones. Registrar cambio detectado → incisos afectados → suspensión si procede → revisión → publicación → reevaluación. Tratar fechas de ATENA y adopción regional/nacional de HS 2028 como **a verificar** hasta contar con respaldo oficial específico; preparar correlaciones potencialmente múltiples.

23. **Media · Ejes 1 y 8 — El interruptor “sin redeploy” carece de un mecanismo definido.**

    **Evidencia:** 02:243 lo promete y 04:131 lo incluye, pero [config.py:170](backend/app/core/config.py:170) cachea la configuración y el compose la toma del entorno al iniciar el proceso.

    **Recomendación:** especificar un interruptor dinámico, auditado y consultado por el backend, o declarar explícitamente que requiere reinicio. Definir alcance: clasificación, cálculo, chat y acceso a resultados previos. Apagar la página por sí sola no apaga la API.

24. **Media · Ejes 4, 7 y 8 — Algunas afirmaciones “comprobadas” no tienen evidencia reproducible en los materiales.**

    **Evidencia:** 02:43 y 03:62 citan “1 de 815” y 13 facturas; 05:61 afirma ocupación de disco del 41%. No encontré consultas ni salidas que respalden esas cifras. 01:14 afirma que no existe API oficial, mientras `:37` dice que no se encontró ninguna. La gratuidad SDDE aparece como comprobada e inferida simultáneamente en 05:15.

    **Recomendación:** adjuntar evidencia saneada, fecha, método y alcance; preservar hashes/capturas de fuentes cambiantes. Cambiar conclusiones negativas absolutas por “no localizada en la investigación”. No califico estas cifras de inventadas: las califico de **no verificables con la evidencia disponible**.

25. **Baja · Eje 1 — Hay detalles de PostgreSQL que deben corregirse antes de las migraciones.**

    **Evidencia:** 02:44 trata `tsvector` como capacidad ausente; 02:124 presenta `EXCLUDE overlapping (...)` como pseudodefinición sin concretar operadores ni dependencias.

    **Recomendación:** corregir la disponibilidad de búsqueda textual incorporada. Para impedir solapamientos con GiST sobre código/tributo y rango, explicitar operadores, límites del rango y, si se adopta esa implementación, `btree_gist`. La igualdad sobre texto en esa combinación está documentada por [PostgreSQL](https://www.postgresql.org/docs/16/btree-gist.html). Definir además cómo entrarán tratados/regímenes en la clave futura.

## Omisiones

Estas son especificaciones adicionales necesarias, no defectos de una implementación existente.

| Severidad / eje | Omisión y evidencia | Recomendación concreta |
|---|---|---|
| Alta · 2, 6 | Las pruebas de 02:230-236 y 04:141-148 no cubren agotamiento concurrente, pérdida de Redis, independencia de breakers, cancelaciones ni acceso SQL fuera del módulo. | Convertir esos escenarios en criterios de aceptación de F5; incluir pruebas con la cadena proxy real en un entorno controlado. |
| Media · 6 | El banco propuesto no define revisión independiente de etiquetas, agrupación de productos similares ni prevención de reutilización repetida del conjunto de prueba. 03:98-105 solo separa desarrollo/prueba. | Versionar casos y adjudicaciones, dividir por familias de producto/origen del caso y mantener una muestra final reservada. |
| Media · 6 | Error relativo indefinido cuando el real es cero; calibración solo por mediana; “tasas equivalentes” no cubre todas las bases. Evidencia: 03:25,41,56. | Añadir error absoluto, política para cero, colas de error crítico y cobertura por nivel. Medir incertidumbre considerando casos relacionados. |
| Media · 4, 8 | Falta política completa de fecha tributaria, zona horaria, días hábiles, discrepancias Hacienda/BCCR y validación de respuestas. 01:117 deja pendiente la equivalencia; 02:217 solo define fallback. | Persistir fecha efectiva y recepción; validar unidad, serie y fecha; impedir sustituciones silenciosas y definir cuándo bloquear. La equivalencia normativa sigue **a verificar**. |
| Media · 2, 3 | No se define validación de texto explicativo, enlaces y afirmaciones no numéricas del LLM. 02:207 protege códigos y números, pero no todos los mensajes. | Restringir enlaces a fuentes aprobadas y renderizar permisos/supuestos desde datos estructurados. Probar contenido adversarial, también desde catálogos importados. |
| Media · 8 | Faltan trazabilidad de aprobación y reversión de cambios de curaduría; `verified_by` y `evidence` no sustituyen un historial de publicación. Evidencia: 02:122 y `:181-185`. | Registrar antes/después, motivo, autor, evidencia y versión publicada; exigir revisión adicional para cambios de fórmula o de alcance. |

La normativa que los propios documentos dejan abierta —seguro presunto, base del SC respecto de Ley 6946, redondeo, courier y montos vigentes de cargos/específicos— debe conservar la etiqueta **“a verificar”**. La auditoría no la sustituye por una interpretación alternativa.

## Lo que está bien fundamentado

- **Motor determinista separado de la IA.** `aranceles` como dominio independiente y Amvi como consumidor es una decisión sólida; coincide con el precedente de ADR-0012.
- **No reutilizar `procesar_turno`.** Sus dependencias de actor, permisos, auditoría y ejecutores del LMS están comprobadas.
- **Snapshots de tasas y cambio.** Son una base correcta para trazabilidad, aunque necesitan completar el versionado señalado.
- **Validación de códigos en el backend y abstención.** Reducen fallos concretos; deben mantenerse sin confundirlos con exactitud de clasificación.
- **Formulario antes que chat, sin adjuntos, NMF y exclusiones explícitas.** Acotan alcance, costo y evaluación.
- **Separación de métricas y reconocimiento de que aún no hay resultados.** 03 evita presentar porcentajes estimados como mediciones.
- **El ejemplo aritmético de 01 §8 es correcto:** recalculé VA, DAI, Ley 6946, IVA y total de **¢1.730.870,40**. El [instructivo DGA](https://www.hacienda.go.cr/docs/INSTRUCTIVO_CALCULO_OBLIGACION_TRIBUTARIA.pdf) contiene el ejemplo base; eso valida el ejercicio, no la vigencia de sus tasas.
- **Los precios base citados para Luna y Terra sí tienen respaldo oficial.** La aritmética del escenario máximo de chat da US$0,1728, consistente con el redondeo de 05. Lo pendiente es comprobar consumos y hacer cumplir los topes. [Luna](https://developers.openai.com/api/docs/models/gpt-5.6-luna), [Terra](https://developers.openai.com/api/docs/models/gpt-5.6-terra).
- **La curaduría humana está reconocida como dependencia central.** Responsable, suplente y solicitud formal de exportación son medidas pertinentes; requieren convertirlas en un proceso operativo verificable.