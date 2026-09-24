-- Decisiones SOLO PARA LA COPIA LOCAL.
--
-- NO son las decisiones de producción. Existen para poder correr el migrador
-- contra el backup y probar la interfaz con datos reales. Las de verdad se
-- toman con el diagnóstico a la vista y se escriben en
-- `../001_emails.sql`, que es el archivo que va a producción.
--
-- Criterio usado acá, y por qué no sirve para producción:
--
--   * Los seis usuarios sin correo son todos `is_staff`. Se les arma una
--     dirección a partir del nombre de usuario para que puedan entrar. En
--     producción hay que preguntarles su correo real: una dirección inventada
--     es una cuenta a la que nadie puede recuperar el acceso.
--
--   * Del par duplicado, la cuenta 1 (`otto`, staff) conserva el correo y la 26
--     (`pprueba11`, cliente) recibe uno derivado. Parece una cuenta de prueba,
--     pero tiene tres cargas asociadas, así que no se desactiva sin preguntar.
--
--   psql -d amvarmar_legacy -v ON_ERROR_STOP=1 -f decisiones_locales.sql

\set ON_ERROR_STOP on

BEGIN;

\set decidido_por 'entorno local — no aplicar en producción'

CREATE TEMP TABLE decisiones (
    user_id      integer PRIMARY KEY,
    email_nuevo  text,
    desactivar   boolean NOT NULL DEFAULT false,
    motivo       text    NOT NULL
) ON COMMIT DROP;

INSERT INTO decisiones
SELECT u.id,
       lower(u.username) || '@local.amvarmar.com',
       false,
       'Local: correo derivado del usuario para poder probar. En producción, preguntar el real.'
FROM auth_user u
WHERE coalesce(trim(u.email), '') = '';

-- La cuenta cliente del par duplicado recibe una dirección propia.
INSERT INTO decisiones
SELECT u.id,
       lower(u.username) || '@local.amvarmar.com',
       false,
       'Local: duplicado de la cuenta 1; se le da dirección propia para poder probar.'
FROM auth_user u
WHERE u.id = (
    SELECT o.id FROM auth_user o
    WHERE lower(trim(o.email)) IN (
        SELECT lower(trim(email)) FROM auth_user
        WHERE coalesce(trim(email), '') <> ''
        GROUP BY 1 HAVING count(*) > 1
    )
    AND NOT o.is_staff
    ORDER BY o.id DESC LIMIT 1
);

INSERT INTO migracion_correcciones
    (script, tabla, registro_pk, campo, valor_anterior, valor_nuevo, motivo, decidido_por)
SELECT 'local_emails', 'auth_user', u.id::text, 'email',
       u.email, d.email_nuevo, d.motivo, :'decidido_por'
FROM decisiones d JOIN auth_user u ON u.id = d.user_id
ON CONFLICT (script, tabla, registro_pk, campo) DO NOTHING;

UPDATE auth_user u SET email = d.email_nuevo
FROM decisiones d WHERE u.id = d.user_id;

DO $$
DECLARE vacios integer; duplicados integer;
BEGIN
    SELECT count(*) INTO vacios FROM auth_user
    WHERE is_active AND coalesce(trim(email), '') = '';
    SELECT count(*) INTO duplicados FROM (
        SELECT lower(trim(email)) FROM auth_user
        WHERE is_active AND coalesce(trim(email), '') <> ''
        GROUP BY 1 HAVING count(*) > 1
    ) d;
    IF vacios > 0 OR duplicados > 0 THEN
        RAISE EXCEPTION 'Quedan % sin correo y % duplicados', vacios, duplicados;
    END IF;
    RAISE NOTICE 'Correos listos para migrar.';
END $$;

COMMIT;
