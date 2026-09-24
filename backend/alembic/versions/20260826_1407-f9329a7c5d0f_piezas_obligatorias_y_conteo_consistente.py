"""piezas obligatorias y conteo consistente

Dos invariantes que hasta ahora vivían solo en el código, y por eso no se
cumplían:

1. `shipments.package_count` es la SUMA de `shipment_packages.quantity`. El
   migrador insertó las piezas y nunca tocó el contador, así que 243 de las 244
   cargas activas mostraban un total que no correspondía a su desglose.
2. Una carga activa tiene al menos una pieza. Sin esto una carga puede mostrar
   "0 bultos", almacenarse y entrar en un despacho sin que exista desglose
   físico de lo que se está moviendo.

La segunda va como CONSTRAINT TRIGGER diferible y no como CHECK porque el
invariante cruza dos tablas: al crear, la fila de `shipments` existe antes que
sus piezas, y solo tiene sentido exigirlo al COMMIT.

Las cargas que ya estaban sin piezas NO se completan inventando datos: se marcan
para revisión y aparecen en la lista de ADR-0002. El trigger no las toca hasta
que alguien las edite, y ahí sí exige corregirlas.

Revision ID: f9329a7c5d0f
Revises: 8d73b0b79c53
Create Date: 2026-08-26 14:07:00.000000
"""

from collections.abc import Sequence

from alembic import op

revision: str = "f9329a7c5d0f"
down_revision: str | Sequence[str] | None = "8d73b0b79c53"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    conexion = op.get_bind()

    # --- 1. Poner al día el contador ---
    op.execute("""
        UPDATE shipments s
        SET package_count = COALESCE(
            (SELECT sum(sp.quantity) FROM shipment_packages sp WHERE sp.shipment_id = s.id), 0
        )
        WHERE s.package_count IS DISTINCT FROM COALESCE(
            (SELECT sum(sp.quantity) FROM shipment_packages sp WHERE sp.shipment_id = s.id), 0
        )
    """)

    # --- 2. Marcar para revisión lo que quedó sin piezas ---
    #
    # No se inventan piezas: nadie sabe cuántos bultos traía una carga que se
    # registró sin desglose, y poner "1 bulto" para que pase el constraint
    # convierte un dato faltante en un dato falso.
    sin_piezas = conexion.exec_driver_sql("""
        UPDATE shipments s
        SET legacy_review_required = true
        WHERE s.deleted_at IS NULL
          AND NOT EXISTS (SELECT 1 FROM shipment_packages sp WHERE sp.shipment_id = s.id)
          AND NOT s.legacy_review_required
        RETURNING s.shipment_number
    """).scalars().all()

    if sin_piezas:
        print(
            f"  AVISO: {len(sin_piezas)} carga(s) sin piezas quedaron marcadas para "
            f"revisión: {', '.join(sin_piezas[:10])}"
            + (" …" if len(sin_piezas) > 10 else "")
        )

    # --- 3. Trigger que mantiene el contador ---
    #
    # En la base y no en el servicio: cualquier camino que toque piezas —el
    # servicio, el migrador, una corrección a mano— deja el contador correcto.
    # Un contador que solo actualiza una de las tres vías es peor que ninguno,
    # porque parece confiable.
    op.execute("""
        CREATE OR REPLACE FUNCTION sincronizar_package_count() RETURNS trigger AS $$
        DECLARE
            carga_nueva uuid;
            carga_anterior uuid;
        BEGIN
            IF TG_OP = 'DELETE' THEN
                carga_anterior := OLD.shipment_id;
            ELSIF TG_OP = 'INSERT' THEN
                carga_nueva := NEW.shipment_id;
            ELSE
                carga_nueva := NEW.shipment_id;
                carga_anterior := OLD.shipment_id;
            END IF;

            UPDATE shipments
            SET package_count = COALESCE(
                (SELECT sum(quantity) FROM shipment_packages
                 WHERE shipment_id = carga_nueva), 0
            )
            WHERE id = carga_nueva;

            -- Al reasignar una pieza hay dos agregados afectados. Actualizar
            -- solo el destino deja el contador del origen permanentemente
            -- desincronizado y permite que se confirme con cero piezas.
            IF carga_anterior IS DISTINCT FROM carga_nueva THEN
                UPDATE shipments
                SET package_count = COALESCE(
                    (SELECT sum(quantity) FROM shipment_packages
                     WHERE shipment_id = carga_anterior), 0
                )
                WHERE id = carga_anterior;
            END IF;

            RETURN NULL;
        END;
        $$ LANGUAGE plpgsql
    """)
    op.execute("""
        CREATE TRIGGER trg_package_count
        AFTER INSERT OR UPDATE OR DELETE ON shipment_packages
        FOR EACH ROW EXECUTE FUNCTION sincronizar_package_count()
    """)

    # --- 4. Toda carga activa tiene al menos una pieza ---
    #
    # DEFERRABLE INITIALLY DEFERRED: se comprueba al COMMIT, no en cada
    # sentencia. Sin eso, insertar la carga antes que sus piezas —que es el
    # único orden posible, porque las piezas la referencian— fallaría siempre.
    op.execute("""
        CREATE OR REPLACE FUNCTION exigir_al_menos_una_pieza() RETURNS trigger AS $$
        DECLARE
            carga uuid;
            cuantas int;
            activa boolean;
            cargas uuid[];
        BEGIN
            IF TG_TABLE_NAME = 'shipments' THEN
                cargas := ARRAY[COALESCE(NEW.id, OLD.id)];
            ELSIF TG_OP = 'DELETE' THEN
                cargas := ARRAY[OLD.shipment_id];
            ELSIF TG_OP = 'UPDATE'
                  AND NEW.shipment_id IS DISTINCT FROM OLD.shipment_id THEN
                cargas := ARRAY[OLD.shipment_id, NEW.shipment_id];
            ELSE
                cargas := ARRAY[NEW.shipment_id];
            END IF;

            FOREACH carga IN ARRAY cargas LOOP
                SELECT deleted_at IS NULL INTO activa FROM shipments WHERE id = carga;
                -- La carga se borró en esta misma transacción: sus piezas se van
                -- con ella y no hay invariante que sostener.
                IF activa IS NULL OR NOT activa THEN
                    CONTINUE;
                END IF;

                SELECT count(*) INTO cuantas
                FROM shipment_packages WHERE shipment_id = carga;

                IF cuantas = 0 THEN
                    RAISE EXCEPTION
                        'La carga % quedaría sin piezas. Toda carga activa necesita al menos una.',
                        carga
                        USING ERRCODE = 'check_violation';
                END IF;
            END LOOP;

            RETURN NULL;
        END;
        $$ LANGUAGE plpgsql
    """)

    # Sobre `shipment_packages` para la baja o reasignación, y sobre cualquier
    # escritura de `shipments`. Una carga legacy sin piezas puede repararse
    # agregando primero su desglose; no puede seguir avanzando mediante otras
    # actualizaciones mientras el invariante continúe roto.
    op.execute("""
        CREATE CONSTRAINT TRIGGER trg_carga_con_piezas
        AFTER DELETE OR UPDATE OF shipment_id ON shipment_packages
        DEFERRABLE INITIALLY DEFERRED
        FOR EACH ROW EXECUTE FUNCTION exigir_al_menos_una_pieza()
    """)
    op.execute("""
        CREATE CONSTRAINT TRIGGER trg_carga_nace_con_piezas
        AFTER INSERT OR UPDATE ON shipments
        DEFERRABLE INITIALLY DEFERRED
        FOR EACH ROW EXECUTE FUNCTION exigir_al_menos_una_pieza()
    """)


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_carga_nace_con_piezas ON shipments")
    op.execute("DROP TRIGGER IF EXISTS trg_carga_con_piezas ON shipment_packages")
    op.execute("DROP FUNCTION IF EXISTS exigir_al_menos_una_pieza()")
    op.execute("DROP TRIGGER IF EXISTS trg_package_count ON shipment_packages")
    op.execute("DROP FUNCTION IF EXISTS sincronizar_package_count()")
    # `package_count` conserva los valores puestos al día. Devolverlo a la
    # cuenta de filas sería restaurar el error, no el estado anterior.
