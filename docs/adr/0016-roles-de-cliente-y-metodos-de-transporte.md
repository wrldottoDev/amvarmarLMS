# ADR-0016: Paridad de los roles de cliente y catálogo cerrado de métodos

- **Fecha:** 2026-08-26
- **Estado:** Aprobado
- **Aprobado por:** AMVARMAR (decisión de negocio)
- **Enmienda:** ADR-0004 (matriz de roles y permisos)

## Contexto

Dos decisiones que salieron de la revisión operativa previa a la reescritura.

**Los roles de cliente estaban escalonados.** `CLIENT_USER` era un subconjunto
estricto de `CLIENT_ADMIN`: le faltaban `users.manage`, `audit_logs.read`,
`reports.export`, `shipments.cancel.prealert` y las preferencias de notificación
de empresa.

En la práctica eso significaba que una empresa con una sola cuenta no podía dar
de alta a la segunda. Quien recibía el acceso inicial —si le tocaba
`CLIENT_USER`— quedaba sin poder crear a sus compañeros y tenía que pedírselo a
Operaciones. La distinción tampoco reflejaba cómo trabajan las empresas
clientes de AMVARMAR, que son equipos chicos donde todos hacen de todo.

**`PICKUP` estaba en el catálogo de métodos de transporte.** No describe un modo
de transporte sino quién retira la mercancía, que es otra dimensión del envío.
Mezclarlos hacía imposible responder "¿cuántos despachos salieron por mar este
mes?" sin decidir a mano qué hacer con los retiros.

## Decisión

### 1. Los dos roles de cliente comparten la misma matriz

`CLIENT_USER` y `CLIENT_ADMIN` tienen **exactamente** los mismos permisos: 13,
sobre el alcance `ORGANIZATION` de su empresa. Ambos pueden crear y corregir
cargas, cancelar prealertas, subir los documentos que les corresponden, crear y
cancelar solicitudes de despacho, gestionar los usuarios de su empresa, leer su
auditoría y exportar sus informes.

Es **un solo conjunto** en el catálogo (`_CLIENT_PERMS`), no dos que casualmente
coinciden. Dos definiciones separadas vuelven a divergir en cuanto alguien
agrega un permiso a una sola de ellas.

**Los dos códigos de rol se conservan.** La distinción puede volver a tener
contenido —una empresa grande podría querer separar quién administra usuarios— y
unificarlos ahora obligaría a migrar las asignaciones existentes para nada.

**Ninguno recibe permisos de transición logística.** Mover una carga por la
cadena es trabajo de quien la tiene físicamente. Un cliente que pudiera marcarla
`STORED` estaría afirmando que llegó a una bodega que no maneja. Hay una prueba
que lo vigila explícitamente.

### 2. Solo existen `SEA`, `AIR` y `LAND`

`PICKUP` se retira del enum, del schema, del CHECK de base y de la interfaz.

La migración **aborta si encuentra filas con ese valor** y dice cuántas hay y
cómo listarlas. No hay traducción correcta: un retiro en mostrador pudo salir
por cualquier medio, o por ninguno, e inventarla falsearía el historial de esos
despachos. En la base de producción migrada no había ninguna.

## Consecuencias

- `CLIENT_USER` gana cinco permisos. Nadie pierde ninguno, así que no hay
  cuentas que se queden sin acceso a algo que ya usaban.
- Una empresa cliente puede administrarse sola desde el primer usuario.
- Cualquier permiso nuevo para clientes se agrega una vez y llega a los dos
  roles. El riesgo que queda es el inverso —dar de más sin querer— y lo cubre la
  prueba que verifica que ningún rol de cliente tenga transiciones.
- El `CHECK` de la base es la última defensa del catálogo de métodos: si alguien
  reintroduce el valor en Python, la restricción lo frena igual.
