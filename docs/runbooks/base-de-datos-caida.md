# PostgreSQL no responde

**Alerta:** `BaseDeDatosSinConexion` — severidad crítica

## Qué significa

La API no puede consultar la base. El sistema está caído para todo efecto
práctico: no se puede ni leer ni escribir.

## Diagnóstico

```bash
pg_isready -h "$PGHOST" -U "$PGUSER" -d "$PGDATABASE"
systemctl status postgresql
df -h                      # disco lleno: causa frecuente y silenciosa
journalctl -u postgresql --since "30 min ago" | tail -50
```

```sql
-- ¿Conexiones agotadas?
SELECT count(*), (SELECT setting::int FROM pg_settings WHERE name = 'max_connections') AS maximo
FROM pg_stat_activity;

-- ¿Consultas bloqueadas?
SELECT pid, now() - query_start AS duracion, state, left(query, 80)
FROM pg_stat_activity
WHERE state <> 'idle' AND now() - query_start > interval '30 seconds'
ORDER BY duracion DESC;
```

## Causas por frecuencia

1. **Disco lleno.** PostgreSQL deja de aceptar escrituras. Liberar espacio —
   respaldos viejos y logs son lo primero que mirar.
2. **Conexiones agotadas.** Un despliegue con demasiadas réplicas, o conexiones
   que no se devuelven al pool. Reiniciar la API libera las suyas.
3. **Una consulta bloqueando al resto.** Terminarla con
   `SELECT pg_terminate_backend(<pid>)`.
4. **El servicio está caído.** Reiniciar y mirar por qué murió.

## Qué NO hacer

- **No reiniciar PostgreSQL a ciegas** si el disco está lleno: puede no volver a
  arrancar. Liberar espacio primero.
- **No restaurar un respaldo** por una caída de servicio. Restaurar es para
  pérdida o corrupción de datos, no para un servicio que no arranca. Ver
  [restaurar-backup.md](restaurar-backup.md).
