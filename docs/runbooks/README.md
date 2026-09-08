# Runbooks

Qué hacer cuando algo falla. Cada uno se escribe para alguien despierto a las
3 de la mañana que no construyó el sistema: pasos concretos, comandos que se
copian, y qué **no** hacer.

Toda alerta de `infra/observability/alertas.yml` apunta a un runbook de acá. Una
alerta sin runbook es una alerta que alguien va a ignorar.

| Runbook | Alerta que lo dispara |
|---|---|
| [api-caida.md](api-caida.md) | `ApiCaida` |
| [base-de-datos-caida.md](base-de-datos-caida.md) | `BaseDeDatosSinConexion` |
| [errores-sostenidos.md](errores-sostenidos.md) | `ErroresSostenidos` |
| [latencia.md](latencia.md) | `LatenciaSobreObjetivo` |
| [outbox-atascado.md](outbox-atascado.md) | `OutboxAcumulado`, `OutboxDetenido`, `OutboxEventosAgotados` |
| [correo-no-sale.md](correo-no-sale.md) | `CorreosFallidos` |
| [reuse-de-refresh.md](reuse-de-refresh.md) | `ReuseDeRefreshMasivo` |
| [fallos-de-login.md](fallos-de-login.md) | `FallosDeLoginMasivos` |
| [documento-infectado.md](documento-infectado.md) | `DocumentoInfectado` |
| [storage-no-responde.md](storage-no-responde.md) | `EscaneoAtascado` |
| [restaurar-backup.md](restaurar-backup.md) | `RespaldoVencido`, `EnsayoDeRestauracionFallido` |

## Migración

Estos dos no responden a una alerta: son procedimientos planificados.

| Runbook | Cuándo |
|---|---|
| [cutover.md](cutover.md) | El día del cambio de sistema (Pasos 5.5 y 5.6) |
| [estabilizacion.md](estabilizacion.md) | Las dos semanas siguientes (Paso 5.7) |

## Objetivos comprometidos

| Objetivo | Valor | Dónde se verifica |
|---|---|---|
| Latencia p95 en lecturas | < 500 ms | `LatenciaSobreObjetivo` + prueba de carga |
| Disponibilidad mensual | 99.5% | `up{job="amvarmar-api"}` |
| RPO (pérdida máxima de datos) | 24 h | `RespaldoVencido` a las 26 h |
| RTO (tiempo máximo de recuperación) | 4 h | Duración del ensayo de restauración |
