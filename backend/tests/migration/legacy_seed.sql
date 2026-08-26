-- Datos de prueba para los scripts de corrección del Paso 5.1.
--
-- TODO ACÁ ES INVENTADO. Ni un correo, ni un nombre de empresa, ni un número de
-- WR sale de producción. La semilla reproduce la FORMA de los problemas que
-- reportó el inventario del Paso 0.3 —correos vacíos, correos duplicados, cargas
-- sin cliente, WR con caracteres de captura— porque eso es lo que los scripts
-- tienen que saber resolver. Los conteos reales viven en
-- `docs/migration/inventario.md` y no hacen falta acá: una semilla de prueba se
-- copia y se comparte, así que no debe llevar datos de personas reales.

INSERT INTO core_company (id, name) VALUES
    (1, 'Empresa Alfa'), (2, 'Empresa Beta'), (3, 'Empresa Sin Usuarios');
SELECT setval('core_company_id_seq', 3);

-- Cuatro usuarios con correo vacío, con distinto perfil de uso para que el
-- diagnóstico tenga algo que distinguir.
INSERT INTO auth_user (id, username, email, is_active, is_staff, last_login) VALUES
    (1, 'sin_correo_en_uso',    '', true,  false, now() - interval '3 days'),
    (2, 'sin_correo_inactivo',  '', false, false, NULL),
    (3, 'sin_correo_sin_uso_a', '', true,  false, NULL),
    (4, 'sin_correo_staff',     '', true,  true,  NULL);

-- Dos pares duplicados. El primero difiere solo en mayúsculas, que es el caso
-- que un UNIQUE normal dejaría pasar y CITEXT no.
INSERT INTO auth_user (id, username, email, is_active, is_staff, last_login) VALUES
    (5, 'ana',      'ana.perez@empresa-alfa.example',  true,  false, now() - interval '1 day'),
    (6, 'ana_dup',  'Ana.Perez@Empresa-Alfa.example',  true,  false, NULL),
    (7, 'beto',     'operaciones@empresa-beta.example', true, false, now()),
    (8, 'beto_dup', 'operaciones@empresa-beta.example', false, false, NULL);

-- Usuarios sanos, para comprobar que los scripts no los tocan.
INSERT INTO auth_user (id, username, email, is_active, is_staff)
SELECT n, 'usuario_' || n, 'usuario' || n || '@ejemplo.example', true, false
FROM generate_series(9, 20) AS n;
SELECT setval('auth_user_id_seq', 20);

INSERT INTO core_clientprofile (user_id, company_id) VALUES
    (5, 1), (6, 1), (7, 2), (8, 2), (9, 1), (10, 2), (1, 1);

-- Cargas: las cinco primeras sin cliente asignado.
INSERT INTO core_warehouse (wr_number, company_id, cliente_id, status, invoice, tracking)
SELECT 'WR' || lpad(n::text, 6, '0'),
       CASE WHEN n % 2 = 0 THEN 1 ELSE 2 END,
       CASE WHEN n <= 5 THEN NULL
            WHEN n % 2 = 0 THEN 5 ELSE 7 END,
       CASE WHEN n <= 4 THEN 'APROBADO'
            WHEN n <= 12 THEN 'PENDIENTE'
            ELSE 'COMPLETADO' END,
       'INV-' || n, 'TRK-' || n
FROM generate_series(1, 60) AS n;

-- Dos WR con caracteres de captura: un punto final y una barra vertical. Son las
-- dos formas que hay que saber limpiar, con valores inventados.
INSERT INTO core_warehouse (wr_number, company_id, cliente_id, status) VALUES
    ('WR000901.', 1, 5, 'COMPLETADO'),
    ('WR000902|', 2, 7, 'COMPLETADO');

INSERT INTO core_dispatchrequest (user_id, company_id, status, method)
SELECT 5, 1, 'COMPLETADO', 'MARITIMO' FROM generate_series(1, 10);

INSERT INTO core_dispatchrequestitem (dispatch_id, warehouse_id)
SELECT d.id, w.wr_number
FROM core_dispatchrequest d
CROSS JOIN LATERAL (
    SELECT wr_number FROM core_warehouse
    WHERE cliente_id IS NOT NULL ORDER BY wr_number LIMIT 3
) AS w;

-- Un documento y una pieza sobre el WR mal capturado: es lo que hace que
-- normalizarlo en el legacy falle, y por eso se hace al migrar.
INSERT INTO core_warehousedocument (warehouse_id, file, original_name)
VALUES ('WR000901.', 'warehouse_docs/2026/01/factura.pdf', 'factura.pdf');

INSERT INTO core_piecewarehouse (warehouse_id, type_of, quantity, description)
VALUES ('WR000901.', 'CAJAS', 2, 'Dos cajas');
