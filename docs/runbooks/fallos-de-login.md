# Tasa alta de logins fallidos

**Alerta:** `FallosDeLoginMasivos` — severidad aviso

## Diagnóstico

El motivo está en la etiqueta de la métrica y distingue los casos:

```promql
sum by (motivo) (rate(amvarmar_login_fallido_total[10m]))
```

| Motivo dominante | Lectura |
|---|---|
| `usuario_inexistente` | Enumeración de correos o credenciales de otra filtración |
| `password_incorrecta` | Fuerza bruta contra cuentas reales, o un cliente mal configurado |
| `cuenta_bloqueada` | El bloqueo por intentos está funcionando |
| `cuenta_inactiva` | Alguien intenta entrar con una cuenta dada de baja |

```sql
-- ¿Concentrado en una IP?
SELECT ip_address, count(*), count(DISTINCT actor_user_id) AS usuarios
FROM audit_logs
WHERE action = 'auth.login' AND outcome = 'DENIED'
  AND occurred_at > now() - interval '1 hour'
GROUP BY 1 ORDER BY 2 DESC LIMIT 10;
```

## Qué hacer

**Una IP contra muchos usuarios** — ataque. Bloquear la IP en el borde (nginx o
WAF). El rate limit de la aplicación ya frena por IP, pero cortar antes ahorra
recursos.

**Muchas IPs contra un usuario** — credenciales de esa cuenta circulando.
Contactar a esa persona y forzar cambio de contraseña.

**Un solo usuario, una sola IP, en ráfaga** — casi siempre una integración con
la contraseña vieja, no un ataque.

## Qué NO hacer

- **No subir el umbral de bloqueo** para que dejen de sonar las alertas.
- **No responder distinto** según si el usuario existe. El código verifica
  contra un hash señuelo cuando la cuenta no existe, para gastar el mismo tiempo
  y no filtrar qué correos están registrados. Cualquier "mejora" que devuelva un
  error distinto rompe eso.
