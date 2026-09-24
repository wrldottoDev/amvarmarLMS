# 003 — normalización del WR: se hace al migrar, no en el legacy

Este script existía para limpiar los Warehouse Receipts con caracteres de
captura (`WR105921.`). **Se eliminó tras verificarlo contra los datos reales.**

## Por qué

En el legacy, `wr_number` es la **clave primaria** de `core_warehouse`, y cinco
tablas la referencian:

- `core_warehousedocument`
- `core_piecewarehouse`
- `core_dispatchrequestitem`
- `core_warehouseinvoice`
- `core_dispatchbldocument`

Las cinco claves foráneas son `NO ACTION`, no `ON UPDATE CASCADE`. Cambiar el WR
de una carga que tenga aunque sea un documento **falla**. Y el WR mal capturado
del backup tiene un documento, una pieza y un ítem de despacho.

Hacerlo funcionaría solo actualizando las cinco tablas hijas en la misma
transacción: mucho riesgo sobre datos de producción para un problema que el
migrador resuelve gratis.

## Dónde se resuelve

En `scripts/migrate_legacy.py`, función `normalizar_wr()`. En el esquema nuevo
el WR es una referencia y no la identidad de la carga (ADR-0005), así que
normalizarlo al escribir `shipment_references` no arrastra nada.

El valor original queda igual en el legacy, que sigue siendo el registro de lo
que pasó.
