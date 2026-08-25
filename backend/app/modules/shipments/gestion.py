"""Alta y edición de cargas (CRUD de Operaciones).

Operaciones alimenta el sistema: crea la carga cuando llega el aviso del
proveedor y la corrige a medida que aparecen datos. Es lo que en el sistema
viejo se hacía creando un `Warehouse`.

Dos reglas que separan esto del motor de transiciones:

- **Acá no se cambia el estado.** Crear siempre nace en `PRE_ALERT`, y de ahí en
  adelante manda `service.transicionar()`, que valida el catálogo, exige permiso
  y deja evento. Permitir editar `current_status_code` con un PATCH sería una
  puerta de atrás alrededor de todas esas reglas.
- **Editar exige `row_version`.** Dos personas corrigiendo la misma carga: una
  pasa, la otra recibe 409 con la versión actual. Sin eso el segundo guardado
  pisa el primero sin que nadie se entere.
"""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import Conflicto, RecursoNoEncontrado, ReglaDeNegocioViolada
from app.modules.rbac.catalog import Perm
from app.modules.rbac.service import PermisosEfectivos
from app.modules.shipments.models import EventType, ShipmentStatus
from app.modules.shipments.service import SinPermisoParaTransicion, VersionDesactualizada


class DatosInvalidos(ReglaDeNegocioViolada):
    code = "DATOS_DE_CARGA_INVALIDOS"


class NoSePuedeEditar(Conflicto):
    code = "CARGA_NO_EDITABLE"


# Estados en los que la carga todavía se puede corregir libremente. Una vez
# despachada, los datos describen algo que ya ocurrió: cambiarlos reescribiría
# la historia en vez de corregir un error de captura.
_EDITABLES: frozenset[str] = frozenset(
    {
        ShipmentStatus.PRE_ALERT,
        ShipmentStatus.IN_TRANSIT,
        ShipmentStatus.RECEIVED,
        ShipmentStatus.STORED,
    }
)


@dataclass(frozen=True)
class DatosDeCarga:
    company_id: UUID
    origin_location_id: UUID
    destination_location_id: UUID
    origin_facility_id: UUID | None = None
    destination_address: str | None = None
    description: str | None = None
    transport_mode: str | None = None
    estimated_arrival_at: datetime | None = None
    weight_kg: Decimal | None = None
    volumetric_weight_kg: Decimal | None = None
    volume_m3: Decimal | None = None
    permit_review_required: bool = False
    assigned_to: UUID | None = None


@dataclass(frozen=True)
class CargaCreada:
    id: UUID
    shipment_number: str
    status: str
    row_version: int


async def _empresa_existe(session: AsyncSession, company_id: UUID) -> bool:
    return bool(
        (
            await session.execute(
                text("SELECT 1 FROM companies WHERE id = :c AND deleted_at IS NULL"),
                {"c": company_id},
            )
        ).scalar_one_or_none()
    )


async def crear(
    session: AsyncSession,
    *,
    datos: DatosDeCarga,
    actor_user_id: UUID,
    permisos: PermisosEfectivos,
) -> CargaCreada:
    """Crea una carga en `PRE_ALERT`.

    Nace siempre en prealerta, sin importar lo que mande quien llama: si la
    carga ya está físicamente en bodega, se avanza con transiciones, que dejan
    su evento. Crear directamente en `STORED` saltaría la línea de tiempo y
    después nadie sabría cuándo llegó.
    """
    if not permisos.permite(Perm.SHIPMENTS_CREATE, company_id=datos.company_id):
        raise SinPermisoParaTransicion(Perm.SHIPMENTS_CREATE)

    if not await _empresa_existe(session, datos.company_id):
        raise DatosInvalidos("La empresa indicada no existe.")

    if datos.origin_location_id == datos.destination_location_id:
        raise DatosInvalidos("El origen y el destino no pueden ser el mismo lugar.")

    try:
        fila = (
            await session.execute(
                text("""
                    INSERT INTO shipments
                        (company_id, created_by, assigned_to, current_status_code,
                         origin_location_id, origin_facility_id,
                         destination_location_id, destination_address,
                         description, transport_mode, estimated_arrival_at,
                         weight_kg, volumetric_weight_kg, volume_m3,
                         permit_review_required)
                    VALUES (:company, :actor, :asignado, :estado,
                            :origen, :bodega, :destino, :direccion,
                            :descripcion, :modo, :eta,
                            :peso, :peso_vol, :volumen, :permiso)
                    RETURNING id, shipment_number, row_version
                """),
                {
                    "company": datos.company_id,
                    "actor": actor_user_id,
                    "asignado": datos.assigned_to,
                    "estado": ShipmentStatus.PRE_ALERT.value,
                    "origen": datos.origin_location_id,
                    "bodega": datos.origin_facility_id,
                    "destino": datos.destination_location_id,
                    "direccion": datos.destination_address,
                    "descripcion": datos.description,
                    "modo": datos.transport_mode,
                    "eta": datos.estimated_arrival_at,
                    "peso": datos.weight_kg,
                    "peso_vol": datos.volumetric_weight_kg,
                    "volumen": datos.volume_m3,
                    "permiso": datos.permit_review_required,
                },
            )
        ).one()
    except IntegrityError as error:
        # Una ubicación o bodega inexistente llega como violación de clave
        # foránea; traducirla evita devolver un 500 por un dato del formulario.
        raise DatosInvalidos(
            "Alguno de los datos referenciados no existe (ubicación, bodega o responsable)."
        ) from error

    await session.execute(
        text("""
            INSERT INTO shipment_events
                (shipment_id, event_type, to_status_code, title, occurred_at, actor_user_id)
            VALUES (:s, :tipo, :estado, :titulo, now(), :actor)
        """),
        {
            "s": fila.id,
            "tipo": EventType.CREATED.value,
            "estado": ShipmentStatus.PRE_ALERT.value,
            "titulo": "Carga creada",
            "actor": actor_user_id,
        },
    )

    return CargaCreada(
        id=fila.id,
        shipment_number=fila.shipment_number,
        status=ShipmentStatus.PRE_ALERT.value,
        row_version=fila.row_version,
    )


# Campos que un PATCH puede tocar. `current_status_code`, `company_id` y
# `shipment_number` quedan fuera a propósito: el estado lo mueve el motor de
# transiciones, y cambiar de empresa o de número reescribiría la identidad de la
# carga en vez de corregirla.
_EDITABLES_CAMPOS: dict[str, str] = {
    "origin_location_id": "origen",
    "origin_facility_id": "bodega",
    "destination_location_id": "destino",
    "destination_address": "direccion",
    "description": "descripcion",
    "transport_mode": "modo",
    "estimated_arrival_at": "eta",
    "weight_kg": "peso",
    "volumetric_weight_kg": "peso_vol",
    "volume_m3": "volumen",
    "permit_review_required": "permiso",
    "assigned_to": "asignado",
}


async def actualizar(
    session: AsyncSession,
    *,
    shipment_id: UUID,
    cambios: dict[str, Any],
    row_version: int,
    actor_user_id: UUID,
    permisos: PermisosEfectivos,
) -> int:
    """Corrige datos de una carga. Devuelve la versión nueva.

    Solo los campos presentes en `cambios` se tocan: mandar el objeto completo
    haría que dos personas editando campos distintos se pisaran igual.
    """
    carga = (
        await session.execute(
            text("""
                SELECT company_id, current_status_code, row_version
                FROM shipments WHERE id = :id AND deleted_at IS NULL
                FOR UPDATE
            """),
            {"id": shipment_id},
        )
    ).one_or_none()

    if carga is None:
        raise RecursoNoEncontrado("Carga no encontrada.")

    if not permisos.permite(Perm.SHIPMENTS_UPDATE, company_id=carga.company_id):
        raise SinPermisoParaTransicion(Perm.SHIPMENTS_UPDATE)

    if carga.current_status_code not in _EDITABLES:
        raise NoSePuedeEditar(
            "Esta carga ya salió de bodega y sus datos no se corrigen desde acá. "
            "Si hay un error, registrelo como corrección en la línea de tiempo.",
        )

    if carga.row_version != row_version:
        raise VersionDesactualizada(
            "La carga fue modificada por otra persona. Recárguela y reintente.",
            details=[{"row_version_actual": carga.row_version}],
        )

    aplicables = {c: v for c, v in cambios.items() if c in _EDITABLES_CAMPOS}
    if not aplicables:
        raise DatosInvalidos("No hay ningún campo editable en la solicitud.")

    asignaciones = ", ".join(f"{campo} = :{_EDITABLES_CAMPOS[campo]}" for campo in aplicables)
    parametros: dict[str, Any] = {
        _EDITABLES_CAMPOS[campo]: valor for campo, valor in aplicables.items()
    }
    parametros |= {"id": shipment_id, "version": row_version}

    consulta = f"""
        UPDATE shipments
        SET {asignaciones}, row_version = row_version + 1, updated_at = now()
        WHERE id = :id AND row_version = :version
        RETURNING row_version
    """  # noqa: S608

    try:
        nueva_version: int = (await session.execute(text(consulta), parametros)).scalar_one()
    except IntegrityError as error:
        raise DatosInvalidos(
            "Alguno de los datos referenciados no existe (ubicación, bodega o responsable)."
        ) from error

    await session.execute(
        text("""
            INSERT INTO shipment_events
                (shipment_id, event_type, title, description, occurred_at, actor_user_id)
            VALUES (:s, :tipo, :titulo, :descripcion, now(), :actor)
        """),
        {
            "s": shipment_id,
            "tipo": EventType.NOTE.value,
            "titulo": "Datos corregidos",
            # Qué se tocó, no los valores: el detalle vive en la auditoría, que
            # sí guarda antes y después con redacción de campos sensibles.
            "descripcion": "Campos actualizados: " + ", ".join(sorted(aplicables)),
            "actor": actor_user_id,
        },
    )

    return nueva_version
