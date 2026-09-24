# Después del cutover: dos semanas de observación

**Paso 5.7.** El sistema viejo no se apaga el día del cambio. Se apaga cuando
hay evidencia de que el nuevo funciona.

## Qué mirar, y qué significa que se mueva

Durante dos semanas, todos los días. No hace falta un turno dedicado: quince
minutos por la mañana alcanzan si las alertas están puestas.

| Señal | Dónde | Qué preocuparse |
|---|---|---|
| Tasa de 5xx | `ErroresSostenidos` | Cualquier repunte sobre el 2% |
| Latencia p95 | `LatenciaSobreObjetivo` | Por encima de 500 ms de forma sostenida |
| Outbox pendiente | `OutboxAcumulado`, `OutboxDetenido` | Cola que no baja: los avisos no salen |
| Eventos agotados | `OutboxEventosAgotados` | Notificaciones que **nunca** llegaron |
| Fallos de login | `FallosDeLoginMasivos` | Gente que no puede entrar tras la migración |
| Documentos sin escanear | `EscaneoAtascado` | Archivos migrados que nadie puede abrir |
| Tickets de soporte | Fuera del sistema | Lo que las métricas no ven |

**Los fallos de login son la señal más importante de la primera semana.** Los
hashes vienen del sistema viejo y se convierten a Argon2id en el primer acceso;
si algo salió mal en esa conversión, se ve acá antes que en ningún otro lado.

```sql
-- Quién todavía no entró desde el cutover. Si son muchos de la misma empresa,
-- es un problema de la migración, no de la gente.
SELECT c.legal_name, count(*) AS sin_entrar
FROM users u
LEFT JOIN company_memberships m ON m.user_id = u.id
LEFT JOIN companies c ON c.id = m.company_id
WHERE u.status = 'ACTIVE' AND u.last_login_at IS NULL
GROUP BY 1 ORDER BY 2 DESC;

-- Cuántos hashes viejos quedan por convertir. Baja solo, con cada login.
SELECT count(*) FROM legacy_password_pending WHERE converted_at IS NULL;
```

## Resolver las cargas marcadas

La migración marcó lo que no supo traducir con certeza, en vez de inventarlo
(ADR-0002). Son **55 cargas**, y hasta que no se resuelvan la Fase 5 no cierra.

```bash
curl -s https://app.amvarmar.com/api/v1/shipments/revision-legacy \
  -H "Authorization: Bearer $TOKEN" | jq
```

Cada una trae el motivo por el que se marcó. Los dos casos son:

- **`PENDIENTE` en el legacy.** No había fecha de recepción, así que no se puede
  saber si la carga llegó a bodega. Se dejó en almacenada. Hay que preguntarle a
  la operación qué pasó de verdad con cada una.
- **`APROBADO` sin solicitud de despacho.** El estado decía aprobado pero no
  había ningún despacho que lo respaldara. Se dejó en almacenada.

Se resuelven así:

```bash
curl -X POST https://app.amvarmar.com/api/v1/shipments/<id>/revision-legacy/resolver \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"nota":"Confirmado con Operaciones: llegó el 3 de marzo y se despachó el 12."}'
```

**Resolver NO cambia el estado.** Si además hay que corregirlo, se hace con una
transición, que valida el catálogo y deja su propio evento. Y la nota es
obligatoria: una marca quitada sin explicación no se puede auditar después.

`legacy_status` no se borra nunca: es el rastro de lo que decía el sistema viejo
y sirve para auditar la traducción años más tarde.

## El sistema viejo

**Apagado pero restaurable, 90 días.** No se borra nada antes de ese plazo.

```bash
# Cortar el acceso, sin destruir nada
sudo systemctl stop amvarmar-legacy
sudo systemctl disable amvarmar-legacy

# El respaldo final del cutover se guarda aparte del ciclo diario, para que la
# purga por antigüedad no se lo lleve.
sudo cp /var/backups/cutover-*.dump /var/backups/retencion-90-dias/
```

Pasados los 90 días y con la operación conforme:

- Retirar el acceso al servidor viejo.
- Archivar el respaldo final en almacenamiento frío.
- Recién entonces, dar de baja la máquina.

## Cuándo se cierra la Fase 5

Las tres cosas, no dos:

1. **Dos semanas sin incidentes críticos.**
2. **Cero cargas con `legacy_review_required`.**
3. **La operación de AMVARMAR confirma que el sistema responde a las cuatro
   preguntas** para las que se construyó:
   - ¿Dónde está una carga?
   - ¿Qué falta para que avance?
   - ¿Cuándo llega?
   - ¿Qué tiene que hacer el cliente?

La tercera es la que importa. Las dos primeras se pueden cumplir con un sistema
que técnicamente funciona y que nadie quiere usar.

```sql
-- El número que cierra la fase
SELECT count(*) FROM shipments WHERE legacy_review_required;
```
