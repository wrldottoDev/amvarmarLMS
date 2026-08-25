# El antivirus detectó un documento infectado

**Alerta:** `DocumentoInfectado` — severidad crítica

## Qué pasó ya, sin intervención

- El documento quedó en `scan_status = 'INFECTED'` y `upload_status = 'FAILED'`.
- **No es descargable.** El sistema es fail closed: solo `CLEAN` habilita la
  descarga.
- Quedó registrado en `audit_logs` con el nombre de la amenaza.
- **El archivo NO se borró.** Es evidencia del incidente.

## Diagnóstico

```sql
SELECT d.id, d.original_name, d.company_id, c.legal_name, u.email AS subio,
       d.created_at, a.reason AS amenaza
FROM documents d
JOIN companies c ON c.id = d.company_id
JOIN users u ON u.id = d.uploaded_by
LEFT JOIN audit_logs a ON a.resource_id = d.id AND a.action = 'document.scan.infected'
WHERE d.scan_status = 'INFECTED'
ORDER BY d.created_at DESC;

-- ¿Ese usuario subió más cosas? Una cuenta comprometida no sube un solo archivo.
SELECT id, original_name, scan_status, created_at
FROM documents WHERE uploaded_by = '<uuid>' ORDER BY created_at DESC LIMIT 20;
```

## Qué hacer

1. **Avisar al cliente.** Casi siempre su equipo tiene una máquina infectada y
   no lo sabe. Es la información más valiosa que se les puede dar.
2. **Revisar el resto de sus subidas** con la consulta de arriba.
3. **Si hay varios infectados del mismo usuario**, tratar la cuenta como
   comprometida: revocar sesiones y forzar cambio de contraseña.
4. **Conservar el archivo.** Si hace falta analizarlo, se descarga desde el
   storage con credenciales de administración, nunca por la aplicación.

## Qué NO hacer

- **No borrar el documento.** Es la evidencia.
- **No reenviar el archivo por correo** para que alguien lo mire.
- **No marcarlo como limpio** porque el cliente diga que es un falso positivo.
  Si de verdad lo es, se verifica contra la firma concreta y se decide con eso
  a la vista, no por confianza.
