-- Corrección de correos vacíos y duplicados (Paso 5.1).
--
-- POR QUÉ BLOQUEA: en el esquema nuevo `users.email` es CITEXT NOT NULL UNIQUE.
-- Seis usuarios con cadena vacía chocan entre sí, y tres pares de duplicados
-- chocan entre ellos. Sin resolverlo, el migrador falla o pierde usuarios.
--
-- CÓMO SE USA: las decisiones se escriben ABAJO, en la tabla `decisiones`. No
-- se toman dentro del script ni se pasan por parámetro: quedan versionadas en
-- git, se revisan como cualquier cambio de código y se pueden auditar después.
--
-- Antes de correr esto:
--   1. `psql -d amvarmar_restore -f docs/migration/diagnostico_5_1.sql`
--   2. Decidir usuario por usuario con esos datos a la vista.
--   3. Llenar `decisiones` y correr PRIMERO contra `amvarmar_restore`.
--
--   psql -d amvarmar_restore -v ON_ERROR_STOP=1 -f docs/migration/correcciones/001_emails.sql

\set ON_ERROR_STOP on

BEGIN;

-- Quién toma la decisión. Se registra junto a cada cambio.
\set decidido_por 'Otoniel Gonzalez'

CREATE TEMP TABLE decisiones (
    user_id       integer PRIMARY KEY,
    email_nuevo   text,      -- NULL = no cambiar el correo
    desactivar    boolean NOT NULL DEFAULT false,
    motivo        text    NOT NULL
) ON COMMIT DROP;

-- ###########################################################################
-- Decisiones tomadas 2026-09-07. Las 6 cuentas de staff (3-8) son internas
-- (is_staff, sin empresa, sin cargas/despachos como cliente): se les pone un
-- correo de prueba temporal a propósito — el real se consigue después y se
-- actualiza con un UPDATE directo, sin volver a correr esta migración. Nadie
-- puede recuperar acceso por ese correo mientras tanto; conocido y aceptado.
--
-- La cuenta 26 (pprueba11, empresa "pruenterprise") es una cuenta de
-- prueba/demo propia con actividad real (3 cargas, 2 despachos) que comparte
-- el correo real de la cuenta 1 (otto, staff, la cuenta admin real). Se
-- queda con un correo de prueba propio; la cuenta 1 conserva el correo real.
-- ###########################################################################

INSERT INTO decisiones VALUES (3, 'meli@local.amvarmar.com', false,
    'Cuenta de staff sin correo capturado; correo de prueba temporal, decisión de Otoniel Gonzalez');
INSERT INTO decisiones VALUES (4, 'kriss@local.amvarmar.com', false,
    'Cuenta de staff sin correo capturado; correo de prueba temporal, decisión de Otoniel Gonzalez');
INSERT INTO decisiones VALUES (5, 'ari@local.amvarmar.com', false,
    'Cuenta de staff sin correo capturado; correo de prueba temporal, decisión de Otoniel Gonzalez');
INSERT INTO decisiones VALUES (6, 'luis@local.amvarmar.com', false,
    'Cuenta de staff sin correo capturado; correo de prueba temporal, decisión de Otoniel Gonzalez');
INSERT INTO decisiones VALUES (7, 'henry@local.amvarmar.com', false,
    'Cuenta de staff sin correo capturado; correo de prueba temporal, decisión de Otoniel Gonzalez');
INSERT INTO decisiones VALUES (8, 'ana@local.amvarmar.com', false,
    'Cuenta de staff sin correo capturado; correo de prueba temporal, decisión de Otoniel Gonzalez');
INSERT INTO decisiones VALUES (26, 'pprueba11@local.amvarmar.com', false,
    'Duplicado del correo real de la cuenta 1 (otto); pprueba11/pruenterprise es cuenta de prueba/demo propia con actividad real, se le da correo de prueba propio');

-- Nada que hacer sin decisiones: mejor abortar que dejar la impresión de que se
-- corrigió algo.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM decisiones) THEN
        RAISE EXCEPTION
            'No hay decisiones cargadas. Corra el diagnóstico y llene la tabla `decisiones`.';
    END IF;
END $$;

-- Un correo nuevo no puede chocar con otro que ya existe ni con otra decisión.
DO $$
DECLARE conflicto text;
BEGIN
    SELECT string_agg(email_nuevo, ', ') INTO conflicto
    FROM (
        SELECT d.email_nuevo
        FROM decisiones d
        WHERE d.email_nuevo IS NOT NULL
          AND EXISTS (
              SELECT 1 FROM auth_user u
              WHERE lower(trim(u.email)) = lower(trim(d.email_nuevo))
                AND u.id <> d.user_id
          )
        UNION ALL
        SELECT email_nuevo FROM decisiones
        WHERE email_nuevo IS NOT NULL
        GROUP BY email_nuevo HAVING count(*) > 1
    ) AS conflictos;

    IF conflicto IS NOT NULL THEN
        RAISE EXCEPTION 'Estos correos chocarían con otro usuario: %', conflicto;
    END IF;
END $$;

-- Registro ANTES de cambiar: si el UPDATE falla, la transacción revierte los dos.
INSERT INTO migracion_correcciones
    (script, tabla, registro_pk, campo, valor_anterior, valor_nuevo, motivo, decidido_por)
SELECT '001_emails', 'auth_user', u.id::text, 'email',
       u.email, d.email_nuevo, d.motivo, :'decidido_por'
FROM decisiones d JOIN auth_user u ON u.id = d.user_id
WHERE d.email_nuevo IS NOT NULL
ON CONFLICT (script, tabla, registro_pk, campo) DO NOTHING;

INSERT INTO migracion_correcciones
    (script, tabla, registro_pk, campo, valor_anterior, valor_nuevo, motivo, decidido_por)
SELECT '001_emails', 'auth_user', u.id::text, 'is_active',
       u.is_active::text, 'false', d.motivo, :'decidido_por'
FROM decisiones d JOIN auth_user u ON u.id = d.user_id
WHERE d.desactivar AND u.is_active
ON CONFLICT (script, tabla, registro_pk, campo) DO NOTHING;

UPDATE auth_user u
SET email = trim(d.email_nuevo)
FROM decisiones d
WHERE u.id = d.user_id AND d.email_nuevo IS NOT NULL;

UPDATE auth_user u
SET is_active = false
FROM decisiones d
WHERE u.id = d.user_id AND d.desactivar;

-- Verificación dentro de la misma transacción: si algo sigue mal, no se
-- confirma nada.
DO $$
DECLARE vacios integer; duplicados integer;
BEGIN
    -- Las cuentas desactivadas pueden quedar sin correo: no se migran como
    -- usuarios con login. Solo importan las que siguen activas.
    SELECT count(*) INTO vacios
    FROM auth_user WHERE is_active AND coalesce(trim(email), '') = '';

    SELECT count(*) INTO duplicados
    FROM (
        SELECT lower(trim(email))
        FROM auth_user
        WHERE is_active AND coalesce(trim(email), '') <> ''
        GROUP BY 1 HAVING count(*) > 1
    ) AS d;

    IF vacios > 0 OR duplicados > 0 THEN
        RAISE EXCEPTION
            'Siguen quedando % activos sin correo y % correos duplicados entre activos',
            vacios, duplicados;
    END IF;
    RAISE NOTICE 'Correos de usuarios activos: sin vacíos y sin duplicados.';
END $$;

COMMIT;
