# La API no responde

**Alerta:** `ApiCaida` — severidad crítica

## Diagnóstico rápido

```bash
curl -fsS localhost:8000/health/live   # ¿el proceso vive?
curl -fsS localhost:8000/health/ready  # ¿sus dependencias responden?
systemctl status amvarmar-api
journalctl -u amvarmar-api --since "15 min ago" | tail -50
```

`live` responde si el proceso está en pie. `ready` además comprueba PostgreSQL y
Redis: si `live` pasa y `ready` no, el problema es una dependencia, no la API.

## Por dónde seguir

| Síntoma | Runbook |
|---|---|
| `ready` dice `database_unavailable` | [base-de-datos-caida.md](base-de-datos-caida.md) |
| `ready` dice `redis_unavailable` | Redis: reiniciar. El login y los permisos dependen de la caché |
| Ni `live` responde | Proceso muerto: reiniciar y buscar en los logs por qué murió |
| Responde pero lento | [latencia.md](latencia.md) |

## Al reiniciar

```bash
systemctl restart amvarmar-api
curl -fsS localhost:8000/health/ready
```

Si el proceso muere una y otra vez al arrancar, casi siempre es configuración:
`Settings` falla rápido si falta una variable obligatoria, a propósito, para no
aceptar tráfico a medio configurar. El error del log dice cuál falta.

## Qué NO hacer

- **No quitar variables de entorno obligatorias** para que arranque. Arranca sin
  clave de firma, sin storage o sin antivirus, y cada una de esas ausencias es
  un fallo peor que estar caído.
