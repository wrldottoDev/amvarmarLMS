"""Rastro de la migración legacy (Paso 5.2).

`legacy_id_map` es la pieza que hace el migrador re-ejecutable: guarda a qué
registro nuevo corresponde cada registro viejo, así que una segunda corrida
reconoce lo ya migrado en vez de duplicarlo.

La clave del legacy se guarda como texto porque no siempre es un entero: la
carga vieja tiene el `wr_number` de clave primaria.
"""

from datetime import datetime
from uuid import UUID

from sqlalchemy import ForeignKey, Index, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base, UUIDPk


class LegacyIdMap(Base):
    __tablename__ = "legacy_id_map"

    id: Mapped[UUIDPk]
    legacy_table: Mapped[str] = mapped_column(String(60))
    legacy_pk: Mapped[str] = mapped_column(String(120))
    new_table: Mapped[str] = mapped_column(String(60))
    new_uuid: Mapped[UUID]

    # Para cuadrar conteos origen contra destino sin volver a la base vieja.
    migrated_at: Mapped[datetime] = mapped_column(server_default=text("now()"))
    # Qué hubo que decidir o inferir. Vacío en la mayoría; con texto en los casos
    # que ADR-0002 manda marcar para revisión.
    nota: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        # Un registro viejo se migra una sola vez. Es la garantía de
        # idempotencia, y vive en la base y no en el código del migrador.
        Index("uq_legacy_id_map_origen", "legacy_table", "legacy_pk", unique=True),
        Index("ix_legacy_id_map_destino", "new_table", "new_uuid"),
    )


class LegacyUserPassword(Base):
    """Hash PBKDF2 de Django, conservado hasta el primer inicio de sesión.

    ADR: el hash viejo se copia tal cual y se convierte a Argon2id cuando la
    persona entra por primera vez (rehash progresivo del Paso 1.5). Guardarlo
    aparte y no en `users.password_hash` sería otra opción, pero obligaría a
    consultar dos tablas en cada login; se deja en `users` y esta tabla solo
    registra que ese usuario todavía arrastra el hash viejo, para poder medir
    cuántos faltan por convertir.
    """

    __tablename__ = "legacy_password_pending"

    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    legacy_algorithm: Mapped[str] = mapped_column(String(40))
    migrated_at: Mapped[datetime] = mapped_column(server_default=text("now()"))
    converted_at: Mapped[datetime | None]
