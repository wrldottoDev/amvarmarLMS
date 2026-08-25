# El p95 superó los 500 ms

**Alerta:** `LatenciaSobreObjetivo` — severidad aviso

El objetivo del proyecto es p95 < 500 ms en lecturas comunes, sin contar uploads
ni llamadas a proveedores externos.

## Diagnóstico

Qué ruta está lenta:

```promql
topk(5,
  histogram_quantile(0.95,
    sum by (ruta, le) (rate(amvarmar_http_peticion_duracion_segundos_bucket[10m]))))
```

En la base, qué consultas cuestan:

```sql
SELECT left(query, 90), calls, round(mean_exec_time::numeric, 1) AS media_ms,
       round(total_exec_time::numeric) AS total_ms
FROM pg_stat_statements ORDER BY total_exec_time DESC LIMIT 10;
```

## Causas por frecuencia

1. **Falta un índice.** Suele aparecer al crecer los datos: la consulta que iba
   bien con mil filas ya no va con un millón. `EXPLAIN (ANALYZE, BUFFERS)` sobre
   la consulta lenta lo confirma.
2. **Paginación mal usada.** Los listados paginan por cursor a propósito; un
   `OFFSET` grande recorre todo lo anterior en cada página.
3. **Consulta N+1.** Muchas llamadas cortas en vez de una. Se ve en las trazas
   como decenas de spans de base dentro de una misma petición.
4. **El pool de conexiones saturado.** El tiempo se va esperando conexión, no
   ejecutando. La consulta parece rápida y la petición no lo es.

## Qué NO hacer

- **No subir el umbral** sin decidir explícitamente que el objetivo cambió.
- **No agregar caché** encima de una consulta lenta sin entenderla: esconde el
  problema y agrega invalidación, que es un problema nuevo.
