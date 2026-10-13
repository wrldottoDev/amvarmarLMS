# 05 — Costos de desarrollo y operación

> Estado: estimación, 2026-10-12. Cada cifra lleva su estado: **COMPROBADO** (fuente verificada) o **ESTIMADO** (cálculo con supuestos explícitos). Los consumos de tokens son **estimados**: se miden de verdad en F4/F6 (04) y este documento se actualiza.

## 1. Precios comprobados

| Concepto | Precio | Fuente | Estado |
|---|---|---|---|
| `gpt-5.6-luna` (el modelo actual de Amvi, `backend/app/core/config.py:67`), entrada | US$0,20 / 1M tokens | https://developers.openai.com/api/docs/pricing (consultado 2026-10-12) | COMPROBADO |
| ídem, entrada en caché | US$0,02 / 1M tokens | ídem | COMPROBADO |
| ídem, salida (incluye los tokens de razonamiento) | US$1,20 / 1M tokens | ídem | COMPROBADO |
| `gpt-5.6-terra` (alternativa más capaz, si la evaluación la exige) | US$2,00 / 0,20 / 12,00 | ídem | COMPROBADO |
| Cloudflare Turnstile, plan Free | US$0; desafíos ilimitados; hasta 20 widgets | https://developers.cloudflare.com/turnstile/plans/ | COMPROBADO |
| API de tipo de cambio de Hacienda | Gratis, sin token (límite de 10 solicitudes/s) | https://api.hacienda.go.cr/docs | COMPROBADO |
| API SDDE del BCCR | Acceso con registro (token); gratuidad no explícita en el documento | Documento oficial del BCCR (01 §1.7) | Existencia y autenticación COMPROBADAS; gratuidad INFERIDA |
| Fuentes del arancel (COMEX XLSX, SIECA PDF, TICA) | Gratis | 01 §1 | COMPROBADO |
| pg_trgm (búsqueda) | Ya instalado, sin costo | migraciones del repo | COMPROBADO |

Nota: OpenAI publica precios distintos para contextos de más de 272K tokens. Este diseño nunca se acerca a ese tamaño.

## 2. Consumo por consulta (ESTIMADO)

### 2.1 MVP con formulario (F5)

Una clasificación usa 2 llamadas al modelo. El cálculo **no usa IA**.

| Llamada | Entrada (supuesto) | Salida con razonamiento `low` (supuesto) |
|---|---|---|
| Extracción de atributos y términos SAC | unos 1.000 tokens (prompt 800 + descripción ≤500 caracteres) | unos 600 |
| Selección entre candidatos | unos 2.200 (prompt 800 + 20 candidatos × 60 + atributos 200) | unos 400 |
| **Total por clasificación** | **unos 3.200** | **unos 1.000** |

- **Costo:** 3.200 × 0,20/1M + 1.000 × 1,20/1M ≈ **US$0,0018** por clasificación.
- Con un factor de 1,5 por preguntas aclaratorias y reintentos, se presupuesta **US$0,003 por consulta**.

### 2.2 Amvi conversacional (F6)

- **Supuesto de una conversación típica:** 4 turnos × 2,5 llamadas por turno × unos 4.000 tokens de entrada por llamada (prompt, esquemas, historial y resultados de herramientas), con unos 500 de salida por llamada.
- Eso da unos 40.000 de entrada y 5.000 de salida: ≈ **US$0,014 por conversación**.
- **Tope por diseño (peor caso):** con 8 turnos máximos × 6 llamadas × 12.000 de entrada y 1.000 de salida por llamada, ≈ US$0,17 por conversación. El presupuesto global diario corta antes de que eso escale.

## 3. Escenarios mensuales de IA (ESTIMADO)

| Consultas por mes | Formulario (US$0,003) | Chat típico (US$0,014) | Chat peor caso con tope (US$0,17) |
|---|---|---|---|
| 100 | US$0,30 | US$1,40 | US$17 |
| 1.000 | US$3 | US$14 | US$170 |
| 10.000 | US$30 | US$140 | US$1.700 |
| 100.000 | US$300 | US$1.400 | US$17.000 |

**Lectura:**
- Con el modelo actual, el costo de IA es bajo en uso normal.
- El riesgo real es el **abuso**: la columna de peor caso es lo que cuesta un bot sin controles. Por eso el **presupuesto diario y el tope del proyecto de OpenAI** (02 §8) son obligatorios, y no un detalle.
- Si la evaluación (F4) obliga a usar `gpt-5.6-terra`, multiplicar las columnas por unos 10 (precio de lista ×10).

## 4. Infraestructura (ESTIMADO salvo donde se indica)

| Concepto | Costo incremental | Notas |
|---|---|---|
| Servidor | US$0 **condicionado a medición** | Misma VPS, mismo compose, sin servicios nuevos (02 §12). 100.000 consultas por mes son unas 2-3 por minuto **en promedio**, pero lo que importa son las **ráfagas** y la concurrencia, con Postgres, Redis (también broker de Celery) y workers compartidos con el LMS. Antes de abrir (F5): definir la carga pico y el SLO del LMS, medir los dos sistemas juntos y fijar límites de concurrencia. |
| Almacenamiento | US$0 hasta unos 6-9 GB | `tax_estimates`, a unos 5 KB por estimación con purga a 180 días: unos 3 GB de datos en régimen con 100k por mes, más índices, WAL, respaldos y espacio de mantenimiento (factor ×2-3, estimado). La VPS estaba al 41% de 72 GB (COMPROBADO: salida de `infra/backup/vigilar.sh` en la VPS, 2026-10-12T21:52Z). Catálogo: decenas de MB. Purga por lotes. |
| Consultas externas | US$0 | Tipo de cambio: 1-2 consultas por día |
| Antibots | US$0 | Turnstile Free (COMPROBADO) |
| Monitoreo | US$0 | El que ya existe: métricas y `vigilar.sh` |

## 5. Costo humano (ESTIMADO; la tarifa la pone Amvarmar)

| Concepto | Horas | Costo |
|---|---|---|
| Desarrollo del MVP público (F0-F5) | 24-39 días de desarrollo (04) | días × tarifa de desarrollo |
| F6 + F7 | 13-23 días | ídem |
| Verificación inicial de incisos (F2) | 15-50 h de especialista | h × tarifa del especialista |
| Banco de evaluación (F3) | 30-60 h de especialista | ídem |
| **Mantenimiento mensual de tasas** | p. ej. re-verificar 300 incisos cada 90 días ≈ 100 por mes × 5-10 min ≈ **8-17 h/mes** | ídem |
| Re-evaluación con cada cambio de modelo o reglas | 2-4 h por corrida (revisión de errores) | ídem |

**El costo dominante a largo plazo no es la IA:** es la **curaduría humana de las tasas**, mientras no haya una exportación oficial del arancel. Conseguir esa exportación de la DGA (F0) es la mayor palanca de ahorro.

## 6. Cómo reducir costos sin perder confiabilidad

1. **Formulario antes que chat.** Cuesta unas 5 veces menos por consulta y es más fácil de evaluar.
2. **Búsqueda manual de inciso sin IA** (`GET /incisos`) para quien ya conoce su código, y como modo de respaldo cuando se agota el presupuesto.
3. **Caché de clasificaciones** (Redis, 30 días): las preguntas repetidas ("iPhone 15", "laptop") no vuelven a llamar al modelo. La clave incluye la descripción normalizada, **los atributos confirmados y las versiones del catálogo, del prompt y del modelo**. Se invalida al publicar cambios en el catálogo. Solo se cachea la clasificación, nunca un cálculo con tasas o tipo de cambio del día. Tamaño acotado (LRU), porque las descripciones arbitrarias generan muchas claves con pocos aciertos.
4. **Prompt estable al inicio** para aprovechar la entrada en caché (US$0,02 contra US$0,20).
5. **Candidatos compactos:** 20 candidatos con descripciones recortadas, no capítulos enteros.
6. **Topes duros:** llamadas por turno, turnos por conversación, presupuesto diario y tope mensual del proyecto de OpenAI.
7. **Pedir la exportación a la DGA:** reduce las horas de curaduría, que son el costo mayor.
8. **No usar Batch/Flex** (US$0,10/0,60): son para trabajos diferidos, no para un formulario interactivo. Solo sirven para correr la evaluación (F3/F4).
