# Reuse masivo de refresh tokens

**Alerta:** `ReuseDeRefreshMasivo` — severidad crítica

## Qué significa

Alguien usó un refresh token que ya se había canjeado. Como los tokens rotan en
cada uso, un token usado dos veces significa que **existe una copia fuera del
dispositivo legítimo**.

El sistema ya reaccionó solo: al detectarlo revoca la sesión completa, no solo
ese token (Paso 1.6). El usuario legítimo queda deslogueado y el atacante
también. Esa parte no hay que hacerla a mano.

Un reuse aislado suele ser un cliente con mala red reintentando una petición que
sí llegó. **Cinco en diez minutos no.**

## Diagnóstico

```sql
-- Sesiones revocadas por reuse en la última hora
SELECT s.id, s.user_id, u.email, s.ip_address, s.user_agent, s.revoked_at
FROM auth_sessions s JOIN users u ON u.id = s.user_id
WHERE s.revoke_reason = 'REUSE_DETECTED' AND s.revoked_at > now() - interval '1 hour'
ORDER BY s.revoked_at DESC;

-- ¿Un usuario o muchos? Cambia por completo la respuesta.
SELECT u.email, count(*) AS veces, count(DISTINCT s.ip_address) AS ips
FROM auth_sessions s JOIN users u ON u.id = s.user_id
WHERE s.revoke_reason = 'REUSE_DETECTED' AND s.revoked_at > now() - interval '24 hours'
GROUP BY 1 ORDER BY 2 DESC;
```

## Qué hacer

**Un solo usuario, una sola IP** — casi siempre un cliente reintentando. Mirar
el `user_agent`: si es siempre el mismo, es red inestable. Anotar y observar.

**Un solo usuario, varias IPs** — token robado. Actuar:

1. Revocar todas sus sesiones.
2. Forzar cambio de contraseña.
3. Revisar `audit_logs` de ese usuario buscando qué se hizo con la sesión
   robada, sobre todo descargas de documentos y cambios de estado.

```sql
-- Revocar todo lo activo de ese usuario
UPDATE auth_sessions
SET revoked_at = now(), revoke_reason = 'SECURITY_INCIDENT'
WHERE user_id = '<uuid>' AND revoked_at IS NULL;

-- Qué hizo esa sesión
SELECT occurred_at, action, resource_type, resource_id, ip_address, outcome
FROM audit_logs WHERE actor_user_id = '<uuid>' AND occurred_at > '<fecha>'
ORDER BY occurred_at;
```

**Muchos usuarios a la vez** — incidente. Escalar. Considerar que la clave de
firma esté comprometida: rotar `JWT_SIGNING_KEY` invalida **todas** las sesiones
de todos los usuarios de inmediato. Es disruptivo y es la respuesta correcta si
se sospecha de la clave.

## Qué NO hacer

- **No desactivar la detección de reuse** para que pare la alerta. Es el
  control que está funcionando.
- **No asumir que es un falso positivo** sin mirar las IPs. El sistema no
  inventa un token usado dos veces.
