# El correo no sale

**Alerta:** `CorreosFallidos`

## Qué significa

Entregas de notificación en estado `FAILED`. Los avisos in-app **sí llegaron**:
que el correo falle no deja al usuario sin enterarse dentro del sistema. Lo que
falta es el aviso para quien no tiene la plataforma abierta.

## Diagnóstico

```sql
-- Agrupado por motivo: casi siempre es una sola causa
SELECT last_error, count(*), max(created_at) AS mas_reciente
FROM notification_deliveries
WHERE channel = 'EMAIL' AND status = 'FAILED'
GROUP BY 1 ORDER BY 2 DESC;

-- ¿Es un destinatario o todos?
SELECT target, count(*) FROM notification_deliveries
WHERE channel = 'EMAIL' AND status = 'FAILED'
GROUP BY 1 ORDER BY 2 DESC LIMIT 10;
```

```bash
# ¿El relay acepta conexiones?
nc -zv "$SMTP_HOST" "$SMTP_PORT"
```

## Causas

1. **Relay caído o rechazando.** Todos fallan con el mismo error. Los eventos
   siguen en el outbox reintentando con backoff; al volver el relay salen solos.
2. **Credenciales vencidas.** Error de autenticación en `last_error`. Rotar y
   redesplegar.
3. **Un destinatario concreto rebota.** Buzón lleno o dirección inexistente. No
   es un incidente del sistema.

## `SKIPPED` no es un fallo

Una entrega en `SKIPPED` significa que el usuario no tiene correo verificado. Es
deliberado: enviar a una dirección sin verificar filtraría el aviso a quien haya
puesto un correo ajeno al registrarse. No hay nada que arreglar; si el usuario
debería recibirlo, hay que verificar su correo.

## Qué NO hacer

- **No mandar los correos a mano** copiando el contenido. Las plantillas no
  llevan datos de negocio a propósito (ADR-0008); un correo escrito a mano con
  el detalle del caso rompe esa regla.
- **No desactivar el canal de correo** para silenciar la alerta. `EMAIL` es
  obligatorio por ADR-0008.
