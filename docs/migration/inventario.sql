-- Inventario y perfilado de datos legacy (Paso 0.3)
-- Correr SIEMPRE contra la copia restaurada (amvarmar_restore), nunca contra producción.
-- Uso: psql -d amvarmar_restore -f docs/migration/inventario.sql > docs/migration/inventario_raw_$(date +%Y%m%d).txt

\echo '=== 1. Total de warehouses ==='
SELECT count(*) FROM core_warehouse;

\echo '=== 2. Distribución de estados (Warehouse) ==='
SELECT status, count(*) FROM core_warehouse GROUP BY status ORDER BY 2 DESC;

\echo '=== 3. Distribución de estados (DispatchRequest) ==='
SELECT status, count(*) FROM core_dispatchrequest GROUP BY status ORDER BY 2 DESC;

\echo '=== 4. Warehouses sin empresa ==='
SELECT count(*) FROM core_warehouse WHERE company_id IS NULL;

\echo '=== 5. Warehouses sin cliente ==='
SELECT count(*) FROM core_warehouse WHERE cliente_id IS NULL;

\echo '=== 6. Warehouses sin empresa NI cliente (huérfanos totales) ==='
SELECT count(*) FROM core_warehouse WHERE company_id IS NULL AND cliente_id IS NULL;

\echo '=== 7. Usuarios activos sin ClientProfile y sin ser staff ==='
SELECT count(*)
FROM auth_user u
LEFT JOIN core_clientprofile cp ON cp.user_id = u.id
WHERE cp.id IS NULL AND u.is_staff = false AND u.is_active = true;

\echo '=== 8. ClientProfile sin empresa asignada ==='
SELECT count(*) FROM core_clientprofile WHERE company_id IS NULL;

\echo '=== 9. Emails duplicados o vacíos ==='
SELECT lower(email) AS email_lower, count(*)
FROM auth_user
GROUP BY 1
HAVING count(*) > 1 OR lower(email) = ''
ORDER BY 2 DESC;

\echo '=== 10. Formato de hashes de contraseña ==='
SELECT split_part(password, '$', 1) AS algoritmo, count(*)
FROM auth_user
GROUP BY 1;

\echo '=== 11. Campo invoice poblado ==='
SELECT count(*) FROM core_warehouse WHERE invoice IS NOT NULL AND invoice <> '';

\echo '=== 12. Campo tracking poblado ==='
SELECT count(*) FROM core_warehouse WHERE tracking IS NOT NULL AND tracking <> '';

\echo '=== 13. Campo po poblado ==='
SELECT count(*) FROM core_warehouse WHERE po IS NOT NULL AND po <> '';

\echo '=== 14. Campo container poblado ==='
SELECT count(*) FROM core_warehouse WHERE container IS NOT NULL AND container <> '';

\echo '=== 15. WR con formato inconsistente (no A-Z0-9-) ==='
SELECT wr_number FROM core_warehouse WHERE wr_number !~ '^[A-Z0-9-]+$';

\echo '=== 16. WR duplicados (no debería pasar, es PK, pero valida mayúsc/minúsc) ==='
SELECT lower(wr_number), count(*) FROM core_warehouse GROUP BY 1 HAVING count(*) > 1;

\echo '=== 17. Dispatches sin items ==='
SELECT count(*)
FROM core_dispatchrequest d
LEFT JOIN core_dispatchrequestitem i ON i.dispatch_id = d.id
WHERE i.id IS NULL;

\echo '=== 18. Dispatches COMPLETADO sin items (caso grave: viola la regla de negocio del propio modelo) ==='
SELECT count(*)
FROM core_dispatchrequest d
LEFT JOIN core_dispatchrequestitem i ON i.dispatch_id = d.id
WHERE i.id IS NULL AND d.status = 'COMPLETADO';

\echo '=== 19. DispatchRequestItem cuyo warehouse ya no existe (no debería, es PROTECT) ==='
SELECT count(*)
FROM core_dispatchrequestitem i
LEFT JOIN core_warehouse w ON w.wr_number = i.warehouse_id
WHERE w.wr_number IS NULL;

\echo '=== 20. Device tokens activos vs inactivos ==='
SELECT is_active, count(*) FROM core_clientdevice GROUP BY 1;

\echo '=== 21. Device tokens duplicados ==='
SELECT token, count(*) FROM core_clientdevice GROUP BY 1 HAVING count(*) > 1;

\echo '=== 22. PieceWarehouse por tipo ==='
SELECT type_of, count(*), sum(quantity) FROM core_piecewarehouse GROUP BY 1;

\echo '=== 23. Warehouses sin ninguna pieza asociada ==='
SELECT count(*)
FROM core_warehouse w
LEFT JOIN core_piecewarehouse p ON p.warehouse_id = w.wr_number
WHERE p.id IS NULL;

\echo '=== 24. Documentos por tabla (conteo bruto, sin verificar existencia en disco) ==='
SELECT 'core_warehousedocument' AS tabla, count(*) FROM core_warehousedocument
UNION ALL SELECT 'core_warehouseinvoice', count(*) FROM core_warehouseinvoice
UNION ALL SELECT 'core_dispatchbldocument', count(*) FROM core_dispatchbldocument;

\echo '=== 25. Companies sin ningún member ==='
SELECT count(*)
FROM core_company c
LEFT JOIN core_clientprofile cp ON cp.company_id = c.id
WHERE cp.id IS NULL;

\echo '=== FIN ==='
