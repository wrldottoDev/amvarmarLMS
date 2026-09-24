# El outbox se atascó

**Alertas:** `OutboxAcumulado`, `OutboxDetenido`, `OutboxEventosAgotados`

## Qué significa

El outbox guarda los avisos que hay que entregar. Si se acumula, los clientes
no están recibiendo notificaciones aunque el sistema por dentro funcione. **El
negocio no se detiene**: las cargas siguen moviéndose, los despachos siguen
aprobándose. Lo que falta es el aviso.

Las tres alertas son distintas:

- `OutboxAcumulado` (>500 pendientes): el worker no da abasto, o hubo un pico.
- `OutboxDetenido` (evento más viejo >30 min): el worker está parado. Dispara
  aunque haya tres eventos, que es lo que el umbral por cantidad no detecta.
- `OutboxEventosAgotados`: eventos que gastaron sus 6 intentos. **Estos ya no se
  reintentan solos.** Sin intervención, esas notificaciones nunca salen.

## Diagnóstico

```sql
-- Qué hay y desde cuándo
SELECT status, count(*), min(created_at) AS mas_viejo
FROM outbox_events GROUP BY status;

-- Qué está fallando, agrupado por motivo
SELECT event_type, last_error_code, count(*), max(attempt_count) AS intentos
FROM outbox_events
WHERE status IN ('PENDING', 'FAILED') AND attempt_count > 0
GROUP BY 1, 2 ORDER BY 3 DESC;
```

```bash
# ¿El worker está vivo?
celery -A app.workers.app.celery_app inspect active
# ¿Redis responde? Es el broker: sin Redis no hay worker.
redis-cli -u "$REDIS_URL" ping
```

## Causas por orden de frecuencia

1. **El worker está caído.** `OutboxDetenido` con `attempt_count = 0` en todo:
   nadie llegó a intentarlo. Reiniciar el worker.
2. **El relay de correo rechaza.** `last_error_code = EnvioFallido`. Seguir con
   [correo-no-sale.md](correo-no-sale.md).
3. **Un manejador lanza siempre.** Un `event_type` concreto con `attempt_count`
   alto y el resto sano. Es un bug: el evento no se va a entregar por más que se
   reintente.

## Qué hacer

**El worker está caído** — reiniciar. Los eventos se recuperan solos: el reclamo
es un lock de PostgreSQL que se suelta cuando el proceso muere (ADR-0014), así
que no hay nada que desbloquear a mano.

**Un evento agotado que ya se puede entregar** — devolverlo a pendiente:

```sql
UPDATE outbox_events
SET status = 'PENDING', processed_at = NULL, attempt_count = 0, available_at = now()
WHERE id = '<uuid>';
```

**Varios agotados por la misma causa ya resuelta** — acotar SIEMPRE por
`last_error_code` y por fecha. Reactivar todo lo agotado sin filtrar reencola
también los que fallan por un bug y vuelven a agotarse en minutos:

```sql
UPDATE outbox_events
SET status = 'PENDING', processed_at = NULL, attempt_count = 0, available_at = now()
WHERE status = 'FAILED'
  AND last_error_code = 'EnvioFallido'
  AND created_at > now() - interval '2 days';
```

## Qué NO hacer

- **No borrar filas del outbox.** Un evento agotado es la evidencia de una
  notificación que nunca salió. Borrarlo hace desaparecer el problema del
  tablero, no de la realidad.
- **No reencolar sin arreglar la causa.** Si el manejador tiene un bug, los
  eventos vuelven a agotarse y se pierden los 6 intentos otra vez.
- **No levantar `MAX_INTENTOS` durante el incidente.** Más intentos contra un
  proveedor caído es más carga sobre algo que ya está mal.

## Después

Un evento reentregado puede llegar dos veces al destinatario: la entrega es *al
menos una vez* (ADR-0014). Es aceptable y esperado. Lo que no es aceptable es
que no llegue.
