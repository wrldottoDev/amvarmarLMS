-- Diagnóstico previo a las correcciones del Paso 5.1.
--
-- SOLO LEE. No modifica nada. Correr primero contra `amvarmar_restore` (la copia
-- restaurada) y, si hace falta confirmar, también contra producción: leer es
-- seguro y los números tienen que coincidir.
--
--   psql -d amvarmar_restore -f docs/migration/diagnostico_5_1.sql
--
-- El inventario del Paso 0.3 dice CUÁNTOS problemas hay. Esto dice CUÁLES son y
-- trae el contexto para decidir qué hacer con cada uno: sin saber si una cuenta
-- se usa o tiene cargas asociadas, cualquier decisión es a ciegas.

\pset format aligned
\pset null '(nulo)'

\echo ''
\echo '################################################################'
\echo '# 0. COMPROBACIÓN PREVIA DE ESQUEMA'
\echo '################################################################'
\echo 'Las consultas de abajo asumen nombres de columna del legacy. Si alguno no'
\echo 'existe, mejor enterarse acá con un mensaje claro que con un error de'
\echo 'PostgreSQL a mitad de un script de corrección.'
\echo ''

WITH esperadas(tabla, columna) AS (
    VALUES
        ('auth_user', 'email'), ('auth_user', 'username'), ('auth_user', 'is_active'),
        ('auth_user', 'is_staff'), ('auth_user', 'date_joined'), ('auth_user', 'last_login'),
        ('core_warehouse', 'wr_number'), ('core_warehouse', 'company_id'),
        ('core_warehouse', 'cliente_id'), ('core_warehouse', 'status'),
        ('core_warehouse', 'created_at'), ('core_warehouse', 'invoice'),
        ('core_warehouse', 'tracking'),
        ('core_company', 'name'),
        ('core_clientprofile', 'user_id'), ('core_clientprofile', 'company_id'),
        ('core_dispatchrequest', 'user_id'), ('core_dispatchrequest', 'method'),
        -- OJO: es `dispatch_id`, no `dispatch_request_id`. Y `warehouse_id`
        -- apunta al `wr_number`, que es la clave primaria de la carga.
        ('core_dispatchrequestitem', 'dispatch_id'),
        ('core_dispatchrequestitem', 'warehouse_id')
)
SELECT e.tabla, e.columna, 'FALTA — revisar el esquema real' AS problema
FROM esperadas e
WHERE NOT EXISTS (
    SELECT 1 FROM information_schema.columns c
    WHERE c.table_name = e.tabla AND c.column_name = e.columna
);

\echo '(Si la consulta anterior no devolvió filas, el esquema coincide.)'

\echo ''
\echo '################################################################'
\echo '# 1. USUARIOS CON EMAIL VACÍO'
\echo '################################################################'
\echo 'En el esquema nuevo `users.email` es CITEXT NOT NULL UNIQUE, así que'
\echo 'varias filas con cadena vacía chocan entre sí. Hay que decidir uno por uno:'
\echo 'darles un correo real, ponerles uno sintético, o desactivarlos.'
\echo ''

SELECT
    u.id,
    u.username,
    u.is_active,
    u.is_staff,
    u.date_joined::date       AS alta,
    u.last_login::date        AS ultimo_acceso,
    -- Señales para decidir: una cuenta sin accesos y sin cargas es candidata a
    -- desactivarse; una con cargas recientes necesita un correo de verdad.
    (SELECT count(*) FROM core_warehouse w WHERE w.cliente_id = u.id)      AS cargas_como_cliente,
    (SELECT count(*) FROM core_dispatchrequest d WHERE d.user_id = u.id)   AS despachos,
    (SELECT c.name FROM core_clientprofile cp
       JOIN core_company c ON c.id = cp.company_id
      WHERE cp.user_id = u.id LIMIT 1)                                     AS empresa
FROM auth_user u
WHERE coalesce(trim(u.email), '') = ''
ORDER BY u.is_active DESC, cargas_como_cliente DESC, u.id;

\echo ''
\echo '################################################################'
\echo '# 2. EMAILS DUPLICADOS'
\echo '################################################################'
\echo 'Solo uno de cada grupo puede quedarse con el correo. El criterio no lo'
\echo 'decide un script: hay que mirar cuál cuenta se usa de verdad.'
\echo ''

WITH duplicados AS (
    SELECT lower(trim(email)) AS email_norm
    FROM auth_user
    WHERE coalesce(trim(email), '') <> ''
    GROUP BY 1
    HAVING count(*) > 1
)
SELECT
    lower(trim(u.email))      AS email_norm,
    u.id,
    u.username,
    u.is_active,
    u.is_staff,
    u.date_joined::date       AS alta,
    u.last_login::date        AS ultimo_acceso,
    (SELECT count(*) FROM core_warehouse w WHERE w.cliente_id = u.id)      AS cargas_como_cliente,
    (SELECT count(*) FROM core_dispatchrequest d WHERE d.user_id = u.id)   AS despachos,
    (SELECT c.name FROM core_clientprofile cp
       JOIN core_company c ON c.id = cp.company_id
      WHERE cp.user_id = u.id LIMIT 1)                                     AS empresa
FROM auth_user u
JOIN duplicados d ON d.email_norm = lower(trim(u.email))
ORDER BY email_norm, u.last_login DESC NULLS LAST, u.id;

\echo ''
\echo '################################################################'
\echo '# 3. CARGAS SIN CLIENTE ASIGNADO'
\echo '################################################################'
\echo 'Tienen empresa pero no persona. En el esquema nuevo `shipments.created_by`'
\echo 'es obligatorio. Se puede asignar al administrador de esa empresa, pero la'
\echo 'decisión debe quedar escrita: atribuye la carga a alguien que no la creó.'
\echo ''

SELECT
    c.name                      AS empresa,
    count(*)                    AS cargas_sin_cliente,
    min(w.created_at)::date     AS mas_antigua,
    max(w.created_at)::date     AS mas_reciente,
    string_agg(DISTINCT w.status, ', ')  AS estados,
    -- Candidato natural: si la empresa tiene un solo usuario, la atribución es
    -- evidente. Si tiene varios, alguien tiene que elegir.
    (SELECT count(*) FROM core_clientprofile cp WHERE cp.company_id = c.id) AS usuarios_de_la_empresa
FROM core_warehouse w
JOIN core_company c ON c.id = w.company_id
WHERE w.cliente_id IS NULL
GROUP BY c.id, c.name
ORDER BY 2 DESC;

\echo ''
\echo '--- Detalle de esas cargas ---'
\echo ''

SELECT w.wr_number, c.name AS empresa, w.status,
       w.created_at::date AS creada, w.invoice, w.tracking
FROM core_warehouse w
JOIN core_company c ON c.id = w.company_id
WHERE w.cliente_id IS NULL
ORDER BY c.name, w.created_at;

\echo ''
\echo '################################################################'
\echo '# 4. WAREHOUSE RECEIPTS CON FORMATO INCONSISTENTE'
\echo '################################################################'
\echo 'En el legacy el WR es la clave primaria; en el esquema nuevo es una'
\echo 'referencia más (ADR-0005) y la carga lleva su propio número. Aun así se'
\echo 'limpia: un WR con un punto o una barra al final es un error de captura que'
\echo 'impide encontrar la carga buscándolo.'
\echo ''

SELECT
    w.wr_number                                        AS wr_actual,
    upper(regexp_replace(w.wr_number, '[^A-Za-z0-9-]', '', 'g')) AS wr_propuesto,
    c.name                                             AS empresa,
    w.status,
    -- Cuántas filas dependen de este WR. Como es la clave primaria y las cinco
    -- claves foráneas que lo referencian son NO ACTION, cambiarlo en el legacy
    -- fallaría si tiene dependientes. Por eso NO se corrige acá: se normaliza
    -- al migrar, cuando el WR pasa a ser una referencia y no una clave.
    (SELECT count(*) FROM core_warehousedocument d WHERE d.warehouse_id = w.wr_number)
        AS documentos,
    (SELECT count(*) FROM core_piecewarehouse pz WHERE pz.warehouse_id = w.wr_number)
        AS piezas,
    (SELECT count(*) FROM core_dispatchrequestitem i WHERE i.warehouse_id = w.wr_number)
        AS en_despachos
FROM core_warehouse w
LEFT JOIN core_company c ON c.id = w.company_id
WHERE w.wr_number !~ '^[A-Z0-9-]+$'
ORDER BY w.wr_number;

\echo ''
\echo '--- ¿La limpieza crearía duplicados? ---'
\echo 'Si esto devuelve filas, normalizar dos WR distintos los volvería iguales y'
\echo 'hay que resolverlo a mano antes de tocar nada.'
\echo ''

SELECT upper(regexp_replace(wr_number, '[^A-Za-z0-9-]', '', 'g')) AS wr_normalizado,
       count(*), string_agg(wr_number, ' | ') AS originales
FROM core_warehouse
GROUP BY 1
HAVING count(*) > 1;

\echo ''
\echo '################################################################'
\echo '# 5. RESUMEN: LO QUE BLOQUEA LA MIGRACIÓN'
\echo '################################################################'
\echo ''

SELECT 'usuarios con email vacío' AS problema,
       count(*) AS filas,
       'bloquea: users.email es NOT NULL UNIQUE' AS consecuencia
FROM auth_user WHERE coalesce(trim(email), '') = ''
UNION ALL
SELECT 'usuarios con email duplicado',
       count(*),
       'bloquea: users.email es UNIQUE (CITEXT, insensible a mayúsculas)'
FROM auth_user u
WHERE coalesce(trim(u.email), '') <> ''
  AND EXISTS (
      SELECT 1 FROM auth_user o
      WHERE o.id <> u.id AND lower(trim(o.email)) = lower(trim(u.email))
  )
UNION ALL
SELECT 'cargas sin cliente',
       count(*),
       'bloquea: shipments.created_by es NOT NULL'
FROM core_warehouse WHERE cliente_id IS NULL
UNION ALL
SELECT 'WR con formato inconsistente',
       count(*),
       'no bloquea; se normaliza al migrar, no en el legacy (es clave primaria)'
FROM core_warehouse WHERE wr_number !~ '^[A-Z0-9-]+$'
UNION ALL
SELECT 'dispatches sin items',
       count(*),
       'no bloquea: se migran como solicitud vacía o se descartan'
FROM core_dispatchrequest d
WHERE NOT EXISTS (SELECT 1 FROM core_dispatchrequestitem i WHERE i.dispatch_id = d.id);
