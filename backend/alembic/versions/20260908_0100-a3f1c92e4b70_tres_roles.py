"""tres roles: super_admin, admin y cliente

Revision ID: a3f1c92e4b70
Revises: 98958af7401b
Create Date: 2026-09-08 01:00:00.000000

ADR-0017. Cinco roles pasan a tres:

    OPS_ADMIN, OPS_AGENT -> ADMIN
    CLIENT_ADMIN, CLIENT_USER -> CLIENTE

Se renombra `OPS_ADMIN` y `CLIENT_ADMIN` en vez de crear filas nuevas, para no
tocar las asignaciones que ya apuntan a esos `role_id`. Las de los roles que
desaparecen (`OPS_AGENT`, `CLIENT_USER`) se reapuntan al rol que queda, y
recién entonces se borran las filas huérfanas.

El orden importa: `user_role_assignments.role_id` es `ON DELETE RESTRICT`, así
que borrar un rol con asignaciones vivas falla. Reapuntar primero es lo que
hace que el borrado sea seguro.

El índice único parcial `uq_user_role_assignments_global` puede chocar al
reapuntar: si alguien tenía OPS_ADMIN *y* OPS_AGENT, las dos filas quedarían
como (mismo user, mismo rol, company NULL). Por eso el UPDATE ignora las que
ya tendrían un equivalente, y esas se borran después.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "a3f1c92e4b70"
down_revision: str | Sequence[str] | None = "98958af7401b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# (rol que desaparece, rol que absorbe sus asignaciones)
_FUSIONES = (("OPS_AGENT", "ADMIN"), ("CLIENT_USER", "CLIENTE"))


def _fusionar(desde: str, hacia: str) -> None:
    """Reapunta las asignaciones de `desde` a `hacia` y borra el rol vacío."""
    # Duplicados primero: quien ya tiene el rol destino con el mismo alcance no
    # necesita una segunda fila, y el índice único no la aceptaría.
    op.execute(f"""
        DELETE FROM user_role_assignments a
        USING roles viejo, roles nuevo
        WHERE a.role_id = viejo.id
          AND viejo.code = '{desde}'
          AND nuevo.code = '{hacia}'
          AND EXISTS (
              SELECT 1 FROM user_role_assignments otra
              WHERE otra.user_id = a.user_id
                AND otra.role_id = nuevo.id
                AND otra.company_id IS NOT DISTINCT FROM a.company_id
          )
    """)  # noqa: S608

    op.execute(f"""
        UPDATE user_role_assignments a
        SET role_id = (SELECT id FROM roles WHERE code = '{hacia}')
        FROM roles viejo
        WHERE a.role_id = viejo.id AND viejo.code = '{desde}'
    """)  # noqa: S608

    op.execute(
        f"DELETE FROM role_permissions WHERE role_id = (SELECT id FROM roles WHERE code = '{desde}')"
    )  # noqa: S608
    op.execute(f"DELETE FROM roles WHERE code = '{desde}'")  # noqa: S608


def upgrade() -> None:
    # Renombrar antes de fusionar: `_fusionar` busca el destino por su código
    # nuevo, que hasta acá no existía.
    op.execute("""
        UPDATE roles
        SET code = 'ADMIN',
            name = 'Administrador',
            description = 'Personal de AMVARMAR. Registra las cargas que llegan a Miami, '
                          'mueve la cadena logística, aprueba despachos y exige, verifica '
                          'e invalida documentos.',
            allowed_scopes = ARRAY['GLOBAL', 'ASSIGNED']::varchar[]
        WHERE code = 'OPS_ADMIN'
    """)
    op.execute("""
        UPDATE roles
        SET code = 'CLIENTE',
            name = 'Cliente',
            description = 'Empresa cliente y su gente. Ve su inventario, pide despachos '
                          'eligiendo la vía, sube los documentos que le exigen y consulta '
                          'a AMVI. No registra cargas.'
        WHERE code = 'CLIENT_ADMIN'
    """)

    for desde, hacia in _FUSIONES:
        _fusionar(desde, hacia)

    # Los permisos concretos de cada rol los reescribe `scripts/seed_rbac.py`
    # desde el catálogo, que es la fuente única de verdad (ADR-0004). Repetir
    # acá qué permiso lleva cada rol sería una segunda copia que puede
    # divergir; esta migración solo deja los tres roles en pie.


def downgrade() -> None:
    """Devuelve los cinco códigos, sin poder deshacer las fusiones.

    Quién era `OPS_AGENT` y quién `OPS_ADMIN` antes de fusionarlos no queda
    registrado en ningún lado: al reapuntar se pierde. La bajada recrea los
    roles vacíos y renombra los que quedan, que es lo más fiel posible —
    todas las personas quedan en el rol de más capacidad de su par.
    """
    op.execute(
        "UPDATE roles SET code = 'OPS_ADMIN', name = 'Administrador de operaciones', allowed_scopes = ARRAY['GLOBAL']::varchar[] WHERE code = 'ADMIN'"
    )
    op.execute(
        "UPDATE roles SET code = 'CLIENT_ADMIN', name = 'Administrador de empresa cliente' WHERE code = 'CLIENTE'"
    )
    op.execute("""
        INSERT INTO roles (code, name, description, allowed_scopes, is_system)
        VALUES
            ('OPS_AGENT', 'Agente de operaciones', NULL, ARRAY['GLOBAL', 'ASSIGNED']::varchar[], true),
            ('CLIENT_USER', 'Usuario de empresa cliente', NULL, ARRAY['ORGANIZATION']::varchar[], true)
        ON CONFLICT (code) DO NOTHING
    """)
