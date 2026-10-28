# 04 — Plan de implementación

> Estado: propuesta, 2026-10-12. **No se programa nada hasta que se apruebe el plan y se resuelvan las decisiones de [07](07_DECISIONES_PENDIENTES.md).**
> Esfuerzos expresados en **días de desarrollo** (estimación, no medición) y, aparte, en **horas de especialista aduanero**, que no se pueden reemplazar con código.

## Principio de orden

Primero un motor verificable, después datos confiables, después medición, y recién entonces la IA y lo público:

```
F0 decisiones y acceso a datos
 └► F1 motor + casos dorados ──► F2 catálogo curado + tipo de cambio
                                   └► F3 banco de evaluación
                                        └► F4 clasificador (interno, medido)
                                             └► F5 página pública (formulario) ◄── MVP público
                                                  └► F6 Amvi conversacional
                                                  └► F7 tratados y preferencias
F8 operación continua (monitoreo, actualización, HS 2028) — desde F2 en adelante
```

**MVP probable con casos reales:** F0-F4. Es interno, sin exposición pública. Los especialistas de Amvarmar lo prueban con casos reales desde la pantalla de admin.
**MVP público:** F5, solo para las categorías habilitadas por la evaluación.

---

## F0 — Decisiones y acceso a datos

- **Objetivo:** que nada se programe sobre supuestos.
- **Tareas:**
  - resolver las decisiones de 07 que bloquean F1 y F2 (las de cada fase se resuelven antes de esa fase; D11 se decide **con** el piloto de F3/F4, no antes);
  - registrarse en el BCCR SDDE (token);
  - **pedir formalmente a la DGA una exportación periódica del arancel** (el XLSX de COMEX prueba que existe);
  - conseguir una fuente de DUAs reales (D4);
  - nombrar al especialista responsable de la curaduría (D1);
  - leer la Circular ONVVA-002-2002 y los arts. 387-402 del RLGA, y aclarar con un especialista si la Ley 6946 entra en la base del SC (01 §9).
- **Dependencias:** ninguna.
- **Archivos:** solo documentación.
- **Complejidad:** baja técnica, alta organizativa. **Riesgo:** que la DGA no responda; se sigue con la curaduría manual.
- **Criterio de aceptación:** decisiones de F1-F2 respondidas; token del BCCR en `.env`; responsable y suplente de curaduría nombrados; al menos 50 casos reales comprometidos.
- **Esfuerzo:** unos 1-2 días de desarrollo; en el calendario depende de terceros (1-3 semanas).

## F1 — Motor tributario y casos dorados

- **Objetivo:** cálculo correcto al céntimo, independiente de la IA y de la base de datos.
- **Funcionalidades:**
  - `motor.py` con funciones puras: VA en colones, DAI, SC, 6946, específicos porcentuales, G/E, IVA (13/2/1%), cargos fijos opcionales y total;
  - el desglose lleva base, tasa, monto y regla (sección del instructivo);
  - `REGLAS_VERSION`;
  - redondeo con `Decimal` y `ROUND_HALF_UP` a céntimos (supuesto documentado y configurable).
- **Dependencias:** F0 (la duda 6946/SC).
- **Archivos:** `backend/app/modules/aranceles/motor.py`, `backend/tests/unit/test_motor_aranceles.py`.
- **Complejidad:** baja-media. **Riesgo:** reglas mal entendidas del instructivo (tiene errores internos). Por eso cada caso dorado documenta su fuente y cualquier corrección.
- **Criterio de aceptación:**
  - reproduce **todos** los ejemplos utilizables del instructivo DGA (01 §8 y vehículo con SC/GE como prueba de cascada) y **al menos 10 DUAs reales** al ±¢1;
  - sin `float` en el módulo (test que lo verifica).
- **Pruebas:** casos dorados; propiedades (sin tasas, total = 0; DAI monótono en VA); un test por tributo.
- **Entregable:** motor con suite verde en `scripts/ci_local.sh`.
- **Esfuerzo:** 3-5 días.

## F2 — Catálogo arancelario curado y tipo de cambio

- **Objetivo:** tasas confiables con procedencia, y tipo de cambio oficial diario.
- **Funcionalidades:**
  - migraciones de `tariff_sources`, `tariff_lines`, `tariff_rates`, `exchange_rates` (02 §4);
  - importador del XLSX de COMEX: estructura y descripciones, todas las tasas como `UNVERIFIED`;
  - cruce del DAI contra el PDF de SIECA 2025 (las diferencias se marcan);
  - permiso `tariffs.manage`;
  - pantalla de **curaduría** (admin LMS): buscar un inciso, ver sus tasas, abrir TICA en otra pestaña, confirmar o corregir cada tasa con evidencia, marcar exclusiones y notas técnicas;
  - cola de vencidas;
  - tarea diaria del tipo de cambio (Hacienda, con BCCR de respaldo), guardando la fecha efectiva, la de consulta, la fuente y la serie; si las dos fuentes discrepan, alerta en vez de elegir sola;
  - perfiles completos por inciso publicados como unidad, con historial y reversión (02 §4);
  - `confianza.py` con las reglas que **no** dependen del clasificador (perfil, tipo de cambio, exclusiones, permisos, datos faltantes).
- **Dependencias:** F0 (curador, token BCCR), F1.
- **Archivos:**
  - `backend/app/modules/aranceles/{models,consultas,router_admin,tipo_cambio}.py`;
  - `backend/alembic/versions/…`;
  - `backend/scripts/importar_arancel_comex.py`;
  - `backend/app/workers/tasks/aranceles.py` y `backend/app/workers/app.py`;
  - `frontend/src/app/(admin)/aranceles/`.
- **Complejidad:** media. **Riesgo:** que la curaduría no escale. Mitigación: empezar por los **150-300 incisos más probables** para Amvarmar (los que el especialista identifique) y dejar el resto como "no estimable".
- **Criterio de aceptación:**
  - catálogo importado (unos 17k incisos);
  - N incisos priorizados verificados con evidencia;
  - tipo de cambio guardado a diario durante 1 semana sin fallas;
  - una estimación interna reproducible desde `tax_estimates`.
- **Pruebas:** importador (conteos y formatos de 12 dígitos), solapamiento de vigencias rechazado por la base, tarea de tipo de cambio con un proveedor HTTP falso, permisos de curaduría.
- **Esfuerzo:** 6-10 días de desarrollo + **15-50 h de especialista** para la verificación inicial (a 5-10 min por inciso; estimación) + mantenimiento recurrente (D2).

## F3 — Banco de evaluación

- **Objetivo:** poder medir antes de exponer.
- **Funcionalidades:**
  - tabla `tax_eval_cases` y pantalla interna de carga con anonimización guiada;
  - `scripts/evaluar_estimador.py` con todas las métricas de 03 §2 e intervalos de Wilson;
  - separación desarrollo/prueba.
- **Dependencias:** F1, F2, casos de F0.
- **Complejidad:** media. **Riesgo:** pocos casos o mal anonimizados. El protocolo de 03 §3.1 se revisa en cada carga.
- **Criterio de aceptación:**
  - al menos 300 casos (20-30 por categoría) o los que haya, informando qué categorías no alcanzan;
  - primera corrida de la métrica **matemática** al 100% (si no, se vuelve a F1).
- **Esfuerzo:** 3-5 días + **30-60 h de especialista** para reunir y anonimizar casos (estimación).

## F4 — Clasificador (interno)

- **Objetivo:** proponer incisos desde lenguaje natural, sin inventar códigos, y medir su exactitud.
- **Funcionalidades:**
  - extracción estructurada con IA (atributos y términos en vocabulario SAC);
  - búsqueda de candidatos con pg_trgm;
  - selección por la IA **restringida a candidatos** y validación en el backend;
  - preguntas aclaratorias, con la clasificación guardada en el servidor (`clasificacion_id`);
  - `confianza.py`: se suman las reglas de clasificación (alternativas, categoría habilitada);
  - **adaptador propio** de OpenAI con configuración, cliente, credenciales y breaker inyectados (no se reutiliza `ProveedorOpenAI` tal cual; 02 §7).
- **Dependencias:** F2, F3.
- **Archivos:** `backend/app/modules/aranceles/{clasificador,confianza}.py`, prompts, tests con `usar_proveedor`.
- **Complejidad:** alta. **Riesgo:** recall bajo de la búsqueda léxica, porque las descripciones SAC son técnicas.
  - Mitigación 1: términos SAC generados por la IA y sinónimos curados.
  - Mitigación 2: si la evaluación muestra recall top-10 insuficiente, evaluar pgvector, que exige cambiar la imagen de Postgres (decisión D12).
- **Criterio de aceptación:**
  - métricas de 03 §2.1 y §2.4 reportadas por categoría con intervalos;
  - **cero códigos fuera de candidatos** (test);
  - decisión documentada de qué categorías se habilitan (03 §3.4).
- **Esfuerzo:** 5-8 días + ciclos de evaluación.

## F5 — Página pública (formulario) y endurecimiento de seguridad — **MVP público**

- **Objetivo:** abrir al público de forma segura y honesta.
- **Funcionalidades:**
  - **requisito previo:** arreglo de `real_ip` de Cloudflare en nginx y uvicorn (también beneficia al login);
  - endpoints públicos de 02 §6;
  - límites por IP y presupuesto global diario de tokens;
  - Turnstile (D6);
  - `limit_req` en nginx;
  - interruptor **dinámico** en Redis (02 §12), no una variable de entorno;
  - rol SQL de mínimos privilegios para el módulo público;
  - página `(publico)/calculadora` mobile-first: formulario, candidatos, desglose, supuestos, fuentes, nivel, rango, descargo, y cargos logísticos aparte;
  - resolver D9 (`ProveedorSesion` en páginas públicas) sin cambiar la autenticación;
  - purga a 180 días.
- **Dependencias:** F4. Un lanzamiento **sin IA** (D13) puede saltarse F4, pero **no** F1, F2, F3 (métricas matemática y tributaria), la política de confianza ni los controles públicos de esta fase. Medir antes de exponer vale para los dos caminos.
- **Archivos:**
  - `aranceles/router_publico.py`, `backend/app/main.py` (montar el router);
  - `infra/produccion/nginx/*.conf`, `infra/produccion/docker-compose.yml`;
  - `frontend/src/app/(publico)/`.
- **Complejidad:** media. **Riesgo:** abuso o costo, y que la gente lea la estimación como una cifra oficial. Mitigación: los controles de 02 §8 y el lenguaje de 03 §4.
- **Criterio de aceptación:**
  - test sin token funciona;
  - un token del LMS no da acceso a nada más;
  - 429 al superar el límite;
  - IP real verificada detrás de Cloudflare;
  - presupuesto agotado apaga la IA y deja el cálculo, **también con llamadas concurrentes** (no se pasa del tope) y con Redis caído;
  - breaker público abierto sin afectar al Amvi del LMS;
  - el rol SQL público no puede leer tablas del LMS;
  - Turnstile rechaza tokens ausentes, reutilizados, vencidos y de otro sitio;
  - errores saneados (ni texto del proveedor ni tokens);
  - Lighthouse móvil sin errores de accesibilidad;
  - revisión del texto legal (D10).
- **Esfuerzo:** 6-9 días.

## F6 — Amvi conversacional público

- **Objetivo:** la experiencia de chat del brief, sobre el mismo motor.
- **Funcionalidades:**
  - endpoint SSE público con registro de **dos** herramientas (`buscar_incisos`, `calcular_estimacion`), proveedor, breaker y límites propios;
  - UI de chat que renderiza el desglose desde el resultado estructurado, no desde el texto.
- **Dependencias:** F5.
- **Complejidad:** media-alta. **Riesgo:** prompt injection y costo por turno. Mitigación: herramientas solo de lectura y tope de llamadas por turno.
- **Criterio de aceptación:**
  - ningún número del chat difiere del motor (test);
  - evaluación conversacional sobre una muestra del banco;
  - costo medido por conversación (05).
- **Esfuerzo:** 5-8 días.

## F7 — Tratados y preferencias arancelarias

- **Objetivo:** mostrar el monto con preferencia cuando el usuario declara tener la prueba de origen.
- **Funcionalidades:** carga de categorías de desgravación por TLC (anexos de SCIJ) y calendarios por año; regla "la preferencia sustituye a DAI + 6946" (01 §4); los dos montos visibles.
- **Dependencias:** F5.
- **Complejidad:** alta en datos. **Riesgo:** aplicar una preferencia sin origen. Mitigación: nunca por defecto, siempre con los dos montos.
- **Esfuerzo:** 8-15 días + carga de calendarios.

## F8 — Operación continua

- **Objetivo:** que la confiabilidad no se degrade con el tiempo.
- **Funcionalidades:**
  - alertas: tipo de cambio sin actualizar, verificaciones por vencer, presupuesto de IA, tasa de errores;
  - vigilancia semanal de las fuentes (SIECA, COMEX, La Gaceta);
  - re-evaluación con cada cambio de modelo o de reglas;
  - **migración a HS 2028** antes del 1-ene-2028, con las correlaciones SIECA;
  - seguimiento de ATENA (reemplazo de TICA).
- **Esfuerzo:** 3-5 días iniciales + mantenimiento: horas de especialista por mes según D2.

---

## Resumen de esfuerzo (estimación)

| Fase | Desarrollo | Especialista |
|---|---|---|
| F0 | 1-2 días | Gestiones con la DGA, el BCCR y los aliados |
| F1 | 3-5 días | Revisión de los casos dorados |
| F2 | 6-10 días | 15-50 h iniciales |
| F3 | 3-5 días | 30-60 h |
| F4 | 5-8 días | Revisión de los errores |
| **MVP interno (F0-F4)** | **18-30 días** | **unas 50-110 h** |
| F5 | 6-9 días | Texto legal |
| **MVP público (F0-F5)** | **24-39 días** | |
| F6 | 5-8 días | |
| F7 | 8-15 días | Carga de tratados |
| F8 | 3-5 días + continuo | Mensual |

Son rangos de planificación, no compromisos. Los días de calendario dependen sobre todo de F0 (terceros) y de la disponibilidad del especialista.
