# ADR-0011: Pertenencia de usuarios a empresas y modelado del personal interno

- **Fecha:** 2026-08-24
- **Estado:** Aprobado
- **Aprobado por:** Otoniel González (revisión técnica) + AMVARMAR (decisión de negocio)

## Contexto

Al construir el Paso 1.3 (users, companies, company_memberships) aparecieron dos preguntas que ningún ADR
anterior resuelve y que cambian el esquema:

1. La tabla `company_memberships` del documento de arquitectura tiene `UNIQUE(company_id, user_id)`, lo que
   permite que un usuario pertenezca a varias empresas. El sistema legacy no lo permite: `ClientProfile` es
   `OneToOneField(User)` con un solo `company_id`.
2. El personal interno de AMVARMAR (`SUPER_ADMIN`, `OPS_ADMIN`, `OPS_AGENT`) no pertenece a ninguna empresa
   cliente. El documento de arquitectura no dice si tienen fila en `company_memberships` ni cómo se
   distinguen de un cliente.

## Decisión

**1. Un usuario pertenece a una sola empresa.** Se conserva el comportamiento del sistema actual.
La restricción efectiva es `UNIQUE(user_id)` sobre `company_memberships`, no `UNIQUE(company_id, user_id)`.

La tabla se mantiene con la forma de una relación muchos-a-muchos (fila propia con `id`, `status`,
`is_primary`, `joined_at`) aunque hoy se comporte como uno-a-uno. Habilitar multi-empresa en el futuro es
eliminar una restricción `UNIQUE`, no rediseñar el modelo ni migrar datos.

Consecuencia práctica: el backend siempre sabe en qué empresa opera quien consulta, sin necesidad de un
concepto de "empresa activa" ni de cambio de contexto por request. `GET /me` (Paso 1.8) devuelve la empresa
del usuario, no una lista con una seleccionada.

**2. El personal interno no tiene membership.** Su acceso viene exclusivamente de una asignación de rol con
alcance `GLOBAL` (ADR-0004). La regla queda binaria y sin campo redundante:

| Tipo de usuario | `company_memberships` | Asignación de rol |
|---|---|---|
| Cliente (`CLIENT_ADMIN`, `CLIENT_USER`) | Tiene fila | Alcance `ORGANIZATION` |
| Interno (`SUPER_ADMIN`, `OPS_ADMIN`, `OPS_AGENT`) | **Sin fila** | Alcance `GLOBAL` (o `ASSIGNED` para agentes) |

No se replica el `is_staff` de Django como columna booleana. El documento de arquitectura ya lo prohíbe
explícitamente ("No se usa `is_staff` como autorización"): la autorización sale del rol, no de un flag.

## Alternativas consideradas

- **Multi-empresa desde el inicio** (un contador que atiende a varios clientes con un solo login). Descartada
  por ahora: obliga a que cada request resuelva un contexto de empresa activa, y todo el aislamiento por
  empresa — la regla de seguridad central del proyecto — pasa a depender de ese contexto. Complejidad real
  sin caso de uso confirmado. La tabla ya soporta habilitarlo después.
- **AMVARMAR como una fila más en `companies`**, con el staff como miembros de esa empresa. Descartada:
  mezcla "empresa cliente" (a la que pertenecen cargas y documentos) con "la empresa operadora", y obligaría
  a excluir esa fila de todo listado de clientes.

## Consecuencias

- `company_memberships` lleva `UNIQUE(user_id)` en vez de `UNIQUE(company_id, user_id)`. Se documenta en la
  migración por qué la restricción es más estricta que el documento de arquitectura.
- Las pruebas de aislamiento por empresa (Paso 1.4, 2.2) pueden asumir una sola empresa por usuario.
- Migración legacy (Paso 5.2): `ClientProfile` → `company_memberships` es un mapeo 1:1 directo, sin necesidad
  de resolver a qué empresa asignar un usuario con varias.
- Los usuarios legacy con `is_staff = true` se migran **sin** fila en `company_memberships`, con asignación
  de rol `GLOBAL`. El rol concreto (`OPS_ADMIN` vs `OPS_AGENT`) se decide durante la migración; el legacy no
  tiene esa distinción.
- Un usuario interno no puede tener una carga "propia": el scope `OWN` de ADR-0004 solo tiene sentido para
  usuarios con membership.
