-- Deshacer las correcciones del Paso 5.1.
--
-- Existe porque `migracion_correcciones` guarda el valor anterior de cada
-- cambio. Un script de corrección sin vuelta atrás obliga a restaurar un
-- respaldo entero para deshacer un correo mal puesto.
--
-- Se revierte POR SCRIPT, no todo junto:
--
--   psql -d amvarmar_restore -v ON_ERROR_STOP=1 -v script=001_emails \
--        -f docs/migration/correcciones/999_revertir.sql

\set ON_ERROR_STOP on

BEGIN;

-- El nombre del script se pasa a una tabla temporal en vez de usarlo dentro del
-- bloque DO: psql NO sustituye `:variables` dentro de texto entre `$$`, así que
-- allí `:'script'` llegaría como literal y la comparación fallaría en silencio.
CREATE TEMP TABLE objetivo ON COMMIT DROP AS SELECT :'script'::text AS script;

DO $$
DECLARE
    nombre text := (SELECT script FROM objetivo);
    fila record;
    total integer;
BEGIN
    SELECT count(*) INTO total FROM migracion_correcciones WHERE script = nombre;

    IF total = 0 THEN
        RAISE EXCEPTION 'No hay correcciones registradas del script %', nombre;
    END IF;

    -- En orden inverso al aplicado, por si dos cambios tocaron el mismo campo
    -- del mismo registro.
    FOR fila IN
        SELECT * FROM migracion_correcciones
        WHERE script = nombre
        ORDER BY id DESC
    LOOP
        -- El valor anterior puede ser NULL (por ejemplo un `cliente_id` que no
        -- estaba asignado); `format` con %L lo escribe como NULL, no como la
        -- cadena 'NULL'.
        EXECUTE format(
            'UPDATE %I SET %I = %L WHERE id = %L::integer',
            fila.tabla, fila.campo, fila.valor_anterior, fila.registro_pk
        );
    END LOOP;

    DELETE FROM migracion_correcciones WHERE script = nombre;
    RAISE NOTICE 'Revertidas % correcciones del script %', total, nombre;
END $$;

COMMIT;
