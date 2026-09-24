# Correcciones de datos legacy — Paso 5.1

**Objetivo del paso:** los problemas del inventario quedan resueltos **en el
sistema viejo**, no parcheados dentro del migrador. Un migrador lleno de
excepciones para datos rotos es un migrador que nadie puede volver a correr.

**Regla del paso:** cada corrección se hace con un script registrado, nunca a
mano en producción sin traza.

## Qué hay que arreglar, según el inventario del Paso 0.3

| Problema | Filas | ¿Bloquea la migración? |
|---|---|---|
| Usuarios con correo vacío | 6 | **Sí** — `users.email` es `NOT NULL UNIQUE` |
| Usuarios con correo duplicado | 6 (3 pares) | **Sí** — `users.email` es `UNIQUE` sobre `CITEXT` |
| Cargas sin cliente | 17 | **Sí** — `shipments.created_by` es obligatorio |
| WR con formato inconsistente | 2 | No, pero impide encontrar la carga por su WR |
| Archivos referenciados que faltan | 0 | — |
| Dispatches sin items | 0 | — |
| Usuarios sin ClientProfile | 0 | — |

Nueve de veintitrés usuarios no entran tal cual. Uno de los correos duplicados
es el de la cuenta con la que se administra el sistema, así que conviene mirar
ese par primero: desactivar la equivocada deja sin acceso a quien administra.

Los valores concretos —qué correo, qué usuario, qué WR— están en
`docs/migration/inventario.md` y los muestra el diagnóstico. No se repiten en
estos scripts ni en las pruebas: son datos de personas reales y cuantos menos
archivos los lleven, mejor.

## Orden de ejecución

**Todo se corre primero contra `amvarmar_restore`** (la copia restaurada) y solo
después contra producción, con el mismo script y el mismo bloque de decisiones.

```bash
# 1. Ver QUÉ hay que decidir. Solo lee; es seguro correrlo donde sea.
psql -d amvarmar_restore -f docs/migration/diagnostico_5_1.sql

# 2. Crear la tabla de registro. Idempotente.
psql -d amvarmar_restore -v ON_ERROR_STOP=1 -f docs/migration/correcciones/000_registro.sql

# 3. Decidir usuario por usuario y ESCRIBIR las decisiones dentro de
#    001_emails.sql, en el bloque marcado. Luego:
psql -d amvarmar_restore -v ON_ERROR_STOP=1 -f docs/migration/correcciones/001_emails.sql

# 4. Cargas sin cliente. Se puede correr sin asignar nada (ver abajo).
psql -d amvarmar_restore -v ON_ERROR_STOP=1 -f docs/migration/correcciones/002_cargas_sin_cliente.sql

# 5. WR mal capturados. No necesita decisiones.
psql -d amvarmar_restore -v ON_ERROR_STOP=1 -f docs/migration/correcciones/003_wr_formato.sql

# 6. Volver a correr el inventario y comparar con el original.
psql -d amvarmar_restore -f docs/migration/inventario.sql > /tmp/inventario_despues.txt
```

## Por qué las decisiones se escriben DENTRO de los scripts

No se pasan por parámetro ni se teclean en una sesión de `psql`. Van en un bloque
del propio archivo, se comitean, y se revisan como cualquier cambio de código.
Un correo asignado a la persona equivocada es un error que hay que poder
rastrear seis meses después, y una sesión interactiva no deja nada.

## Las decisiones que hay que tomar

### Correos vacíos (6 usuarios)

Para cada uno: correo real, o desactivar. **No hay tercera opción**: un correo
sintético en una cuenta activa es una cuenta a la que nadie puede recuperar el
acceso ni avisarle nada. El diagnóstico trae último acceso, cargas asociadas y
empresa para decidir con datos.

### Correos duplicados (3 pares)

Solo uno de cada par se queda con el correo. El otro necesita uno propio o se
desactiva. El diagnóstico ordena por último acceso, que suele bastar para ver
cuál cuenta se usa de verdad.

### Cargas sin cliente (17)

Dos caminos, y el segundo suele ser el correcto:

- **Asignar** el usuario de la empresa. Sirve cuando la empresa tiene un solo
  usuario. Atribuye la carga a alguien que no la creó, así que cada asignación
  lleva motivo y queda registrada.
- **No asignar nada.** El migrador las marca con `legacy_review_required = true`
  (ADR-0002) y el dato queda como lo que es: desconocido. Correr
  `002_cargas_sin_cliente.sql` con el bloque vacío hace exactamente esto y avisa
  cuántas quedaron.

## Deshacer

`migracion_correcciones` guarda el valor anterior de cada cambio, así que
cualquier script se revierte sin restaurar un respaldo entero:

```bash
psql -d amvarmar_restore -v ON_ERROR_STOP=1 -v script=001_emails \
     -f docs/migration/correcciones/999_revertir.sql
```

## Qué protege cada script

Todos corren dentro de una transacción y verifican el resultado **antes** de
confirmar. Si la verificación falla, no queda nada aplicado.

- `001_emails` aborta si un correo nuevo chocaría con otro usuario o si dos
  decisiones usan el mismo, y si al terminar sigue habiendo vacíos o duplicados
  entre cuentas activas.
- `002_cargas_sin_cliente` aborta si se asigna un usuario que no pertenece a la
  empresa de la carga — es la fuga entre empresas que el modelo de alcances
  existe para impedir.
- `003_wr_formato` aborta si normalizar fusionaría dos WR distintos o dejaría
  alguno vacío.

## Estos scripts están probados

`backend/tests/migration/test_correcciones_legacy.py` levanta un esquema legacy
con el mismo perfil de problemas del inventario y corre **estos mismos archivos**,
sin copiarlos ni adaptarlos. Comprueba que corrigen lo que dicen, que abortan
ante cada error que declaran impedir, que registran el valor anterior, que
correrlos dos veces no duplica el registro y que la reversión devuelve los datos
a como estaban.

```bash
cd backend && pytest tests/migration/ -m slow
```
