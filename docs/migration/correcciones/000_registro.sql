-- Tabla de registro de correcciones (Paso 5.1).
--
-- La regla del paso: cada corrección se hace con un script registrado, nunca a
-- mano en producción sin traza. Esta tabla es esa traza — guarda el valor
-- anterior, así que además permite deshacer.
--
--   psql -d <base> -f docs/migration/correcciones/000_registro.sql
--
-- Es idempotente: correrla dos veces no rompe nada.

CREATE TABLE IF NOT EXISTS migracion_correcciones (
    id              bigserial PRIMARY KEY,
    aplicada_en     timestamptz NOT NULL DEFAULT now(),
    script          text        NOT NULL,
    tabla           text        NOT NULL,
    registro_pk     text        NOT NULL,
    campo           text        NOT NULL,
    valor_anterior  text,
    valor_nuevo     text,
    -- Por qué se hizo. Sin esto, dentro de seis meses nadie sabrá si un correo
    -- sintético fue una decisión o un accidente.
    motivo          text        NOT NULL,
    -- Quién decidió. No es el usuario de base de datos: es la persona.
    decidido_por    text        NOT NULL
);

COMMENT ON TABLE migracion_correcciones IS
    'Cambios hechos sobre los datos legacy antes de migrar (Paso 5.1). '
    'Guarda el valor anterior para poder revertir.';

-- Evita aplicar dos veces la misma corrección al mismo campo del mismo registro.
CREATE UNIQUE INDEX IF NOT EXISTS uq_migracion_correcciones_una_por_campo
    ON migracion_correcciones (script, tabla, registro_pk, campo);
