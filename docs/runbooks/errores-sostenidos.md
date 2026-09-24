# Más del 2% de las peticiones falla

**Alerta:** `ErroresSostenidos` — severidad crítica

## Diagnóstico

En Prometheus, qué ruta está fallando:

```promql
topk(5, sum by (ruta, codigo) (rate(amvarmar_http_peticion_total{codigo=~"5.."}[5m])))
```

En los logs, el error concreto. Todo log lleva `request_id`, y ese id viaja en
la cabecera `X-Request-ID` de la respuesta: si un usuario reporta un fallo y
puede leerlo, se encuentra la petición exacta.

```bash
journalctl -u amvarmar-api --since "15 min ago" | grep -F '"status": 5' | tail -30
```

## Por dónde seguir

| Patrón | Causa probable |
|---|---|
| Una sola ruta falla | Bug en ese endpoint. Mirar el último despliegue |
| Todas fallan | Dependencia caída: [base-de-datos-caida.md](base-de-datos-caida.md) |
| Empezó justo tras un despliegue | Revertir primero, investigar después |
| Solo rutas de documentos | [storage-no-responde.md](storage-no-responde.md) |

## Qué hacer

Si coincide con un despliegue, **revertir antes de investigar**. Diagnosticar con
producción rota alarga el incidente sin hacerlo más fácil.

## Qué NO hacer

- **No subir el umbral de la alerta** para que deje de sonar.
- **No convertir el 500 en 200** para bajar la métrica. La petición sigue
  fallando y ahora el cliente no se entera.
