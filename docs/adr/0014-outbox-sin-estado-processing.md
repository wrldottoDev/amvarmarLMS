# ADR-0014: El outbox no tiene estado `PROCESSING`

- **Fecha:** 2026-08-25
- **Estado:** Aprobado
- **Aprobado por:** Otoniel González (revisión técnica)

## Contexto

El Paso 4.1 del plan de trabajo pide `outbox_events` con
`status` en (`PENDING`, `PROCESSING`, `DONE`, `FAILED`), y en la misma lista de
requisitos pide reclamar los eventos con `SELECT ... FOR UPDATE SKIP LOCKED`.

Las dos cosas juntas se contradicen, y el gate del propio paso lo deja en
evidencia: **"matar el worker a mitad de proceso → al reiniciar, el evento se
procesa y una sola vez"**.

Con un `PROCESSING` persistido en una columna, matar el worker deja el evento
marcado como en curso para siempre. Nadie lo vuelve a reclamar, porque ya no
está `PENDING`. Haría falta un proceso extra que busque eventos "atascados en
PROCESSING desde hace más de X" y los devuelva — con el X mal elegido, o se
duplican entregas o se retrasan horas.

## Decisión

`status` tiene tres valores: `PENDING`, `DONE`, `FAILED`. **No hay
`PROCESSING`.**

El reclamo es el lock de fila de PostgreSQL. `FOR UPDATE SKIP LOCKED` marca el
evento como tomado mientras dure la transacción del worker, y lo suelta solo
si el proceso muere o la transacción termina. No hay estado inconsistente
posible porque no hay estado: es el motor de base de datos el que lo sostiene.

Consecuencias directas:

- Varios workers en paralelo no se pisan, sin coordinación adicional.
- Un worker muerto devuelve sus eventos de inmediato, sin reaper ni timeout.
- No hay ningún valor de tiempo que ajustar.

## Sobre la entrega "exactamente una vez"

No existe. Un evento puede entregarse dos veces: si el worker entrega y muere
antes de commitear el `DONE`, al reiniciar lo entrega de nuevo. Eso es
inevitable sin transacciones distribuidas con el proveedor externo, que ni SMTP
ni FCM soportan.

Lo que el outbox garantiza es **al menos una vez**, que es lo que resuelve el
problema real: el legacy manda el correo desde `transaction.on_commit` y si el
proceso muere en el medio, la notificación se pierde y nadie se entera.

La consecuencia práctica cae sobre el Paso 4.2: **los manejadores tienen que
tolerar recibir el mismo evento dos veces.** Está anotado en el código de
`procesar_lote()` y probado en `test_el_worker_muerto_devuelve_sus_eventos`,
que verifica explícitamente la doble entrega en vez de esconderla.

## Deduplicación en el origen

`dedup_key` con índice único parcial resuelve un problema distinto: que el
mismo hecho de negocio se escriba dos veces. Se usa `ON CONFLICT DO NOTHING`,
que lo resuelve en la base y por eso también aguanta dos procesos concurrentes,
no solo dos llamadas del mismo proceso.

La clave incluye la versión resultante de la fila
(`shipment:{id}:v{row_version}`), no solo el identificador: si usara únicamente
el id, el segundo cambio de estado de una carga no generaría aviso.

## Alternativas consideradas

- **`UPDATE ... SET status = 'PROCESSING'` al reclamar.** Descartada: sobrevive
  a la muerte del worker y exige un reaper con timeout, que es otro parámetro
  mal ajustado esperando a fallar.
- **`PROCESSING` con `heartbeat_at`.** Descartada por lo mismo, con más piezas:
  reproduce a mano lo que el lock de PostgreSQL ya hace bien.
- **Cola de mensajes en vez de tabla.** Descartada: perdería la propiedad
  central, que es escribir el evento en la MISMA transacción que el cambio de
  negocio. Un broker externo no participa de esa transacción.

## Consecuencias

- Un evento que agota sus 6 intentos queda en `FAILED` con `last_error_code`.
  **No se descarta**: un evento que desaparece en silencio es exactamente el
  problema que este paso viene a resolver. Falta la alerta operativa sobre
  `FAILED`, que corresponde al Paso 4.3.
- El índice parcial del worker filtra por `status = 'PENDING'` y no por
  `processed_at IS NULL`: un evento agotado tampoco tiene fecha de procesado,
  así que el filtro viejo lo habría devuelto para siempre.
- Un `CHECK` obliga a que `status = 'PENDING'` y `processed_at IS NULL` sean
  equivalentes, para que un bug no deje el rastro incompleto.
