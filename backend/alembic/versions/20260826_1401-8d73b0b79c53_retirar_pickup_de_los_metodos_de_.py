"""retirar PICKUP de los metodos de despacho

El catálogo aprobado deja solo `SEA`, `AIR` y `LAND`. `PICKUP` nunca describió
un modo de transporte sino quién retiraba la mercancía, que es otra dimensión;
mezclarlos hacía imposible saber si un despacho salió por mar o por aire.

**Aborta si existen filas `PICKUP`.** No hay traducción correcta —un retiro en
mostrador pudo salir por cualquier medio, o por ninguno— e inventarla falsearía
el historial de esos despachos. El mensaje dice cuántos hay y cómo listarlos.

Revision ID: 8d73b0b79c53
Revises: 1f93eb9be376
Create Date: 2026-08-26 14:01:36.325604
"""

from collections.abc import Sequence

from alembic import op

revision: str = "8d73b0b79c53"
down_revision: str | Sequence[str] | None = "1f93eb9be376"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_METODOS = ("SEA", "AIR", "LAND")


def _check(metodos: tuple[str, ...]) -> str:
    valores = ", ".join(f"'{m}'" for m in metodos)
    return f"method IN ({valores})"


def upgrade() -> None:
    conexion = op.get_bind()
    pendientes = conexion.exec_driver_sql(
        "SELECT count(*) FROM dispatch_requests WHERE method = 'PICKUP'"
    ).scalar_one()

    if pendientes:
        raise RuntimeError(
            f"Hay {pendientes} solicitudes de despacho con método PICKUP. "
            "No se pueden traducir automáticamente: un retiro en mostrador pudo "
            "salir por cualquier medio. Listalas con\n"
            "  SELECT dispatch_number, company_id, status, requested_at\n"
            "  FROM dispatch_requests WHERE method = 'PICKUP' ORDER BY requested_at;\n"
            "y asignales el método real antes de volver a correr la migración."
        )

    op.drop_constraint(op.f("ck_dispatch_requests_method_valido"), "dispatch_requests", type_="check")
    op.create_check_constraint(
        op.f("ck_dispatch_requests_method_valido"), "dispatch_requests", _check(_METODOS)
    )


def downgrade() -> None:
    op.drop_constraint(op.f("ck_dispatch_requests_method_valido"), "dispatch_requests", type_="check")
    op.create_check_constraint(
        op.f("ck_dispatch_requests_method_valido"),
        "dispatch_requests",
        _check((*_METODOS, "PICKUP")),
    )
