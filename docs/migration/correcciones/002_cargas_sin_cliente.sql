-- Cargas sin cliente asignado (Paso 5.1).
--
-- POR QUÉ IMPORTA: en el esquema nuevo `shipments.created_by` es obligatorio.
-- Son 17 cargas que tienen empresa pero no persona.
--
-- LO QUE HAY QUE ENTENDER ANTES DE CORRER ESTO: asignar un cliente atribuye la
-- carga a alguien que no la creó. Es un dato falso, aunque sea conveniente. Por
-- eso cada asignación lleva motivo y queda registrada, y por eso la alternativa
-- de dejarlas marcadas para revisión es legítima y a veces mejor.
--
-- Dos caminos, y el segundo suele ser el correcto:
--
--   A) Asignar el usuario administrador de la empresa. Sirve cuando la empresa
--      tiene un solo usuario y la atribución es evidente.
--
--   B) No asignar nada acá y dejar que el migrador las marque con
--      `legacy_review_required = true` (ADR-0002). El dato queda como lo que es
--      —desconocido— y alguien lo resuelve con contexto después.
--
--   psql -d amvarmar_restore -v ON_ERROR_STOP=1 -f docs/migration/correcciones/002_cargas_sin_cliente.sql

\set ON_ERROR_STOP on

BEGIN;

\set decidido_por 'PENDIENTE — poner el nombre de quien decide'

-- La clave de la carga es el `wr_number`, no un entero: en el legacy el WR es
-- la clave primaria de `core_warehouse`.
CREATE TEMP TABLE asignaciones (
    wr_number   varchar(50) PRIMARY KEY,
    cliente_id  integer NOT NULL,
    motivo      text    NOT NULL
) ON COMMIT DROP;

-- ###########################################################################
-- LLENAR SOLO CON LAS CARGAS QUE SE VAYAN A ASIGNAR (camino A).
-- Las que no estén acá quedan sin cliente y el migrador las marcará para
-- revisión, que es el camino B.
-- ###########################################################################
--
--   INSERT INTO asignaciones VALUES ('WR105921', 9,
--       'Único usuario de la empresa en el período; confirmado con Operaciones');
--
-- Consulta útil para ver los candidatos de cada empresa:
--
--   SELECT c.name, u.id, u.username, u.email, u.is_active
--   FROM core_clientprofile cp
--   JOIN core_company c ON c.id = cp.company_id
--   JOIN auth_user u    ON u.id = cp.user_id
--   WHERE c.id IN (SELECT company_id FROM core_warehouse WHERE cliente_id IS NULL)
--   ORDER BY c.name, u.id;
--
-- ###########################################################################

-- El cliente asignado tiene que pertenecer a la empresa de la carga. Sin esta
-- comprobación se podría atribuir una carga a alguien de otra empresa, que es
-- exactamente la fuga que el modelo de alcances existe para impedir.
DO $$
DECLARE malas text;
BEGIN
    SELECT string_agg(format('carga %s -> usuario %s', a.wr_number, a.cliente_id), '; ')
    INTO malas
    FROM asignaciones a
    JOIN core_warehouse w ON w.wr_number = a.wr_number
    WHERE NOT EXISTS (
        SELECT 1 FROM core_clientprofile cp
        WHERE cp.user_id = a.cliente_id AND cp.company_id = w.company_id
    );

    IF malas IS NOT NULL THEN
        RAISE EXCEPTION 'Asignaciones a usuarios de otra empresa: %', malas;
    END IF;
END $$;

INSERT INTO migracion_correcciones
    (script, tabla, registro_pk, campo, valor_anterior, valor_nuevo, motivo, decidido_por)
SELECT '002_cargas_sin_cliente', 'core_warehouse', w.wr_number, 'cliente_id',
       NULL, a.cliente_id::text, a.motivo, :'decidido_por'
FROM asignaciones a JOIN core_warehouse w ON w.wr_number = a.wr_number
WHERE w.cliente_id IS NULL
ON CONFLICT (script, tabla, registro_pk, campo) DO NOTHING;

UPDATE core_warehouse w
SET cliente_id = a.cliente_id
FROM asignaciones a
WHERE w.wr_number = a.wr_number AND w.cliente_id IS NULL;

DO $$
DECLARE restantes integer;
BEGIN
    SELECT count(*) INTO restantes FROM core_warehouse WHERE cliente_id IS NULL;
    RAISE NOTICE 'Cargas que siguen sin cliente: % (irán con legacy_review_required)', restantes;
END $$;

COMMIT;
