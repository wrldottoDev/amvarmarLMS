# ADR-0015: Observabilidad, respaldos y objetivos no funcionales

- **Fecha:** 2026-08-25
- **Estado:** Aprobado
- **Aprobado por:** Otoniel González (revisión técnica)

## Contexto

El Paso 4.3 pide métricas, trazas, alertas, respaldos con prueba de restauración
y runbooks. Al construirlo aparecieron tres decisiones que no estaban resueltas
en el plan y una que lo contradice.

## Decisiones

### 1. `/metrics` no es público

Expone nombres de rutas internas, volumen de usuarios y patrones de fallo de
login: reconocimiento gratis para quien quiera atacar el sistema. Se protege con
un token propio, distinto del JWT, porque quien raspa es Prometheus y no una
persona con sesión. La comparación es en tiempo constante.

**Sin token configurado el endpoint queda deshabilitado fuera de local**, y
responde 404 en vez de 403: que el endpoint exista ya es información. Es fail
closed a propósito, para que un despliegue que olvide la variable no termine
publicando las métricas.

### 2. Los indicadores derivados de la base se calculan al raspar, con caché

Cuánto hay pendiente en el outbox o cuántas entregas fallaron no son números de
este proceso sino del estado compartido, así que no se pueden contar en memoria:
con varias réplicas, cada una tendría su propio valor y ninguno sería el real.

Se consultan al raspar, con caché de 15 segundos para que varias réplicas
raspadas a la vez no se conviertan en una consulta por segundo. Si la base no
responde se deja el valor anterior y se registra: **un raspado no puede tumbar
`/metrics`**, porque entonces se perderían también las métricas de proceso justo
cuando más falta hacen.

### 3. La etiqueta de ruta es la plantilla, nunca la URL

`/shipments/{shipment_id}`, no `/shipments/<uuid concreto>`. Con la URL concreta
cada carga sería una serie temporal distinta y Prometheus se quedaría sin memoria
en semanas. Las rutas que no coinciden con ninguna plantilla se agrupan como
`desconocida`: devolver la URL cruda dejaría que cualquiera creara series
infinitas pidiendo rutas inexistentes.

Los buckets del histograma están puestos alrededor del objetivo del proyecto
(500 ms). Los buckets por defecto saltan de 0.5 a 1 s, y con eso no se puede
distinguir 510 ms de 990 ms — justo el rango donde se decide si se cumple.

### 4. Push (FCM) sigue fuera; no hay `device_tokens`

Confirmado por ADR-0008 y ADR-0010. Se anota acá porque el plan del Paso 4.2 lo
pedía y alguien podría leer su ausencia como un olvido.

## Respaldos

- Formato `custom` (`-Fc`), no SQL plano: permite restaurar tablas sueltas,
  comprime, y `pg_restore` puede paralelizar.
- Se escribe a un temporal y se renombra al final. Un respaldo interrumpido no
  debe quedar con el nombre definitivo, o la próxima restauración usaría un
  archivo truncado creyéndolo bueno.
- Suma de control calculada al respaldar y verificada antes de restaurar.
- La purga por antigüedad corre **después** de que el respaldo nuevo esté
  completo: borrar primero dejaría una ventana sin ningún respaldo válido.

**El ensayo de restauración cuenta filas, no solo verifica que `pg_restore`
termine.** Restaurar únicamente el esquema también "funciona", y sería un
desastre descubrirlo durante una emergencia. También compara `alembic_version`:
una base restaurada con esquema viejo deja la aplicación sin arrancar.

**`pg_dump` no puede respaldar un servidor más nuevo que él**, y el error que da
no dice qué hacer. El script lo comprueba antes y da un mensaje accionable. Es un
caso real, no hipotético: la máquina de desarrollo de este proyecto tiene
herramientas de PostgreSQL 14 contra un servidor 16. Una máquina de respaldos con
esa combinación produce cero respaldos y nadie lo nota hasta que hace falta
restaurar.

**Los documentos no están en el respaldo.** `pg_dump` guarda la base, no los
archivos de S3/MinIO. El bucket se respalda por separado y se restaura aparte;
está documentado en el runbook porque es la clase de detalle que se descubre
tarde.

## Alternativas consideradas

- **Exponer `/metrics` sin autenticación, restringido por red.** Descartada: dos
  controles independientes son mejores que uno, y la restricción de red se
  configura fuera del repositorio, donde nadie la revisa en un code review.
- **Actualizar los indicadores desde una tarea periódica en vez de al raspar.**
  Descartada: los gauges viven por proceso, así que la tarea actualizaría los del
  worker y no los de las réplicas de la API.
- **Un estado `PROCESSING` para saber qué está entregándose.** Ya descartado en
  ADR-0014; se menciona porque la métrica de outbox pendiente podría tentar a
  reintroducirlo.
- **`SELECT count(*)` sin caché en cada raspado.** Descartada: con varias
  réplicas y raspado cada 15 s, es carga constante sobre la base para un número
  que no cambia tan rápido.

## Consecuencias

- Servicios nuevos en `docker compose`, bajo el perfil `observabilidad` para que
  no arranquen en desarrollo: Prometheus, Alertmanager, Pushgateway y Grafana.
- Los trabajos programados publican su resultado por Pushgateway: terminan antes
  del siguiente raspado, así que no se los puede raspar.
- 14 alertas, cada una con su runbook. Una alerta sin runbook es una alerta que
  alguien va a ignorar.
- Las trazas solo se activan si hay colector configurado. Exportar a un destino
  que no existe llena los logs de errores de conexión y no aporta ninguna traza.
- Los objetivos no funcionales tienen prueba ejecutable: p95 medido contra la app
  real y ensayo de restauración contra un PostgreSQL de la misma versión que
  producción. El p95 medido en local queda cerca de 11 ms contra un objetivo de
  500 ms; es un piso, no una promesa de producción, donde hay red, más datos y
  concurrencia.
