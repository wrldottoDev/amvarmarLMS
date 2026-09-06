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
from app.modules.shipments.models import (
    EventType,
    PackageType,
    ReferenceType,
    ShipmentStatus,
)
from app.modules.shipments.policies import (
    validar_identificador_comercial,
    validar_referencia_permitida,
)
from app.modules.shipments.service import (
    DatosTransicion,
    SinPermisoParaTransicion,
    VersionDesactualizada,
    transicionar,
)


class DatosInvalidos(ReglaDeNegocioViolada):
    code = "DATOS_DE_CARGA_INVALIDOS"


class NoSePuedeEditar(Conflicto):
    code = "CARGA_NO_EDITABLE"


# Estados en los que la carga todavía se puede corregir libremente. Una vez
# despachada, los datos describen algo que ya ocurrió: cambiarlos reescribiría
# la historia en vez de corregir un error de captura.
# Estados en los que una carga puede nacer. Solo los que describen mercancía que
# ya está en manos de AMVARMAR: quien la recibe en mostrador no debería tener que
# crearla en prealerta y avanzarla a mano dos veces. Más adelante no se puede
# nacer —una carga no empieza despachada— y hacia atrás tampoco tiene sentido.
_ESTADOS_INICIALES: frozenset[str] = frozenset(
    {
        ShipmentStatus.PRE_ALERT,
        ShipmentStatus.IN_TRANSIT,
        ShipmentStatus.RECEIVED,
        ShipmentStatus.STORED,
    }
)

_SECUENCIA_ESTADOS_INICIALES: tuple[str, ...] = (
    ShipmentStatus.PRE_ALERT,
    ShipmentStatus.IN_TRANSIT,
    ShipmentStatus.RECEIVED,
    ShipmentStatus.STORED,
)

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
    weight_lb: Decimal | None = None
    volumetric_weight_kg: Decimal | None = None
    volume_m3: Decimal | None = None
    foots_cft: Decimal | None = None
    shipper: str | None = None
    carrier: str | None = None
    permit_review_required: bool = False
    assigned_to: UUID | None = None
    # Identificadores comerciales. Se guardan como referencias de la carga, no
    # como columnas: una carga puede tener varias facturas o varios trackings.
    invoice: str | None = None
    tracking: str | None = None
    po: str | None = None
    container: str | None = None
    wr: str | None = None
    # Los bultos que trae la carga. En el sistema viejo era la sección "Tipos de
    # carga (Piezas)" del formulario de alta, y se perdía si no se cargaba ahí.
    packages: tuple["DatosDeBulto", ...] = ()
    # Estado en el que nace. `None` = prealerta. Solo se aceptan los estados que
    # describen mercancía ya presente: quien recibe en mostrador la carga ya
    # llegó, y obligarlo a crear en prealerta y avanzar a mano son tres clics
    # que nadie da.
    initial_status: str | None = None


@dataclass(frozen=True)
class DatosDeBulto:
    package_type: str
    quantity: int
    description: str | None = None
    weight_kg: Decimal | None = None
    length_cm: Decimal | None = None
    width_cm: Decimal | None = None
    height_cm: Decimal | None = None


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
    """Da de alta una carga.

    La fila siempre nace en PRE_ALERT. Si Operaciones seleccionó un estado
    posterior, se recorren las transiciones intermedias con el motor normal.
    """
    if not permisos.permite(Perm.SHIPMENTS_CREATE, company_id=datos.company_id):
        raise SinPermisoParaTransicion(Perm.SHIPMENTS_CREATE)

    if not await _empresa_existe(session, datos.company_id):
        raise DatosInvalidos("La empresa indicada no existe.")

    if datos.origin_location_id == datos.destination_location_id:
        raise DatosInvalidos("El origen y el destino no pueden ser el mismo lugar.")

    estado_inicial = datos.initial_status or ShipmentStatus.PRE_ALERT.value
    if estado_inicial not in _ESTADOS_INICIALES:
        raise DatosInvalidos(
            "Una carga solo puede crearse en prealerta, en tránsito, recibida o almacenada."
        )

    # La regla del sistema viejo (`WarehouseForm.clean`): sin peso no se puede
    # cotizar ni consolidar, y una carga sin ninguno de los dos entra al
    # inventario como un bulto de masa desconocida.
    if datos.weight_kg is None and datos.weight_lb is None:
        raise DatosInvalidos("Indique el peso, en kilos o en libras.")

    _validar_bultos(datos.packages)

    try:
        fila = (
            await session.execute(
                text("""
                    INSERT INTO shipments
                        (company_id, created_by, assigned_to, current_status_code,
                         origin_location_id, origin_facility_id,
                         destination_location_id, destination_address,
                         description, transport_mode, estimated_arrival_at,
                         weight_kg, weight_lb, volumetric_weight_kg, volume_m3,
                         foots_cft, shipper, carrier, permit_review_required)
                    VALUES (:company, :actor, :asignado, :estado,
                            :origen, :bodega, :destino, :direccion,
                            :descripcion, :modo, :eta,
                            :peso, :peso_lb, :peso_vol, :volumen,
                            :cft, :shipper, :carrier, :permiso)
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
                    "peso_lb": datos.weight_lb,
                    "peso_vol": datos.volumetric_weight_kg,
                    "volumen": datos.volume_m3,
                    "cft": datos.foots_cft,
                    "shipper": datos.shipper,
                    "carrier": datos.carrier,
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

    if (datos.wr or "").strip():
        await validar_referencia_permitida(
            session, shipment_id=fila.id, reference_type=ReferenceType.WR.value
        )
    await _guardar_referencias(session, fila.id, datos)

    # La regla del negocio: lo de Miami lleva WR, lo demás lleva factura. Se
    # comprueba DESPUÉS de guardar las referencias, porque la factura llega
    # como una de ellas.
    await validar_identificador_comercial(
        session,
        shipment_id=fila.id,
        origin_facility_id=datos.origin_facility_id,
        invoice=datos.invoice,
    )

    await _guardar_bultos(session, fila.id, datos.packages)
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

    version = int(fila.row_version)
    if estado_inicial != ShipmentStatus.PRE_ALERT.value:
        limite = _SECUENCIA_ESTADOS_INICIALES.index(estado_inicial)
        for destino in _SECUENCIA_ESTADOS_INICIALES[1 : limite + 1]:
            resultado = await transicionar(
                session,
                shipment_id=fila.id,
                datos=DatosTransicion(
                    to_status=destino,
                    row_version=version,
                    note="Estado inicial registrado por Operaciones.",
                    metadatos={"initial_registration": True},
                ),
                actor_user_id=actor_user_id,
                permisos=permisos,
            )
            version = resultado.row_version

    return CargaCreada(
        id=fila.id,
        shipment_number=fila.shipment_number,
        status=estado_inicial,
        row_version=version,
    )


def _validar_bultos(bultos: tuple[DatosDeBulto, ...]) -> None:
    """Toda carga lleva al menos una pieza, y ninguna pieza es de cero.

    Sin desglose, una carga puede almacenarse y entrar en un despacho sin que
    nadie sepa cuántos bultos se están moviendo. La base tiene el mismo
    invariante con un constraint diferible; esto lo comprueba antes para poder
    devolver un mensaje que diga cuál pieza está mal.
    """
    if not bultos:
        raise DatosInvalidos("Indique al menos una pieza: tipo y cantidad.")

    for numero, bulto in enumerate(bultos, start=1):
        if bulto.quantity < 1:
            raise DatosInvalidos(f"La pieza {numero} tiene cantidad {bulto.quantity}; mínimo 1.")
        if bulto.package_type not in set(PackageType):
            raise DatosInvalidos(f"El tipo de pieza {bulto.package_type} no existe.")

        # Opcionales, pero si vienen tienen que ser reales. Un bulto de cero
        # kilos o cero centímetros es un dato mal capturado, no un bulto.
        for etiqueta, valor in (
            ("peso", bulto.weight_kg),
            ("largo", bulto.length_cm),
            ("ancho", bulto.width_cm),
            ("alto", bulto.height_cm),
        ):
            if valor is not None and valor <= 0:
                raise DatosInvalidos(f"El {etiqueta} de la pieza {numero} debe ser mayor que cero.")


async def _guardar_bultos(
    session: AsyncSession, shipment_id: UUID, bultos: tuple[DatosDeBulto, ...]
) -> None:
    """Las piezas que trae la carga.

    `package_count` no se escribe acá: lo mantiene el trigger
    `trg_package_count` como la suma de las cantidades. En la base y no en el
    servicio porque el migrador y las correcciones a mano también tocan piezas,
    y un contador que solo actualiza una de las tres vías es peor que ninguno.
    """
    for bulto in bultos:
        await session.execute(
            text("""
                INSERT INTO shipment_packages
                    (shipment_id, package_type, quantity, description,
                     weight_kg, length_cm, width_cm, height_cm)
                VALUES (:s, :tipo, :cantidad, :descripcion, :peso, :largo, :ancho, :alto)
            """),
            {
                "s": shipment_id,
                "tipo": bulto.package_type,
                "cantidad": bulto.quantity,
                "descripcion": (bulto.description or "").strip()[:255] or None,
                "peso": bulto.weight_kg,
                "largo": bulto.length_cm,
                "ancho": bulto.width_cm,
                "alto": bulto.height_cm,
            },
        )


async def _guardar_referencias(
    session: AsyncSession, shipment_id: UUID, datos: DatosDeCarga
) -> None:
    """Factura, tracking, PO y contenedor, como referencias de la carga.

    No son columnas porque una carga puede traer varias facturas o varios
    números de rastreo, y en el sistema viejo eso obligaba a meterlos separados
    por comas en un campo de texto.
    """
    for tipo, valor in (
        (ReferenceType.WR, datos.wr),
        (ReferenceType.INVOICE, datos.invoice),
        (ReferenceType.TRACKING, datos.tracking),
        (ReferenceType.PO, datos.po),
        (ReferenceType.CONTAINER, datos.container),
    ):
        limpio = (valor or "").strip()
        if not limpio:
            continue
        await session.execute(
            text("""
                INSERT INTO shipment_references (shipment_id, reference_type, value)
                VALUES (:s, :t, :v) ON CONFLICT DO NOTHING
            """),
            {"s": shipment_id, "t": tipo.value, "v": limpio[:120]},
        )


# Campos que un PATCH puede tocar. `current_status_code`, `company_id` y
# `shipment_number` quedan fuera a propósito: el estado lo mueve el motor de
# transiciones, y cambiar de empresa o de número reescribiría la identidad de la
# carga en vez de corregirla.
# Identificadores comerciales. No son columnas de `shipments` sino filas de
# `shipment_references`, así que se aplican aparte del UPDATE genérico. El
# sistema viejo los editaba desde el mismo formulario y acá también.
_REFERENCIAS_EDITABLES: dict[str, ReferenceType] = {
    "wr": ReferenceType.WR,
    "invoice": ReferenceType.INVOICE,
    "tracking": ReferenceType.TRACKING,
    "po": ReferenceType.PO,
    "container": ReferenceType.CONTAINER,
}

_EDITABLES_CAMPOS: dict[str, str] = {
    "origin_location_id": "origen",
    "origin_facility_id": "bodega",
    "destination_location_id": "destino",
    "destination_address": "direccion",
    "description": "descripcion",
    "transport_mode": "modo",
    "estimated_arrival_at": "eta",
    "weight_kg": "peso",
    "weight_lb": "peso_lb",
    "volumetric_weight_kg": "peso_vol",
    "volume_m3": "volumen",
    "foots_cft": "cft",
    "shipper": "shipper",
    "carrier": "carrier",
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
                SELECT company_id, current_status_code, row_version,
                       (SELECT count(*) FROM shipment_packages p
                        WHERE p.shipment_id = shipments.id) AS package_rows
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

    if carga.package_rows == 0:
        raise DatosInvalidos(
            "Esta carga legacy no tiene piezas. Agregue al menos una pieza antes de corregir "
            "otros datos."
        )

    aplicables = {c: v for c, v in cambios.items() if c in _EDITABLES_CAMPOS}
    referencias = {c: v for c, v in cambios.items() if c in _REFERENCIAS_EDITABLES}

    if not aplicables and not referencias:
        raise DatosInvalidos("No hay ningún campo editable en la solicitud.")

    if not aplicables:
        # Solo cambiaron identificadores. Igual sube la versión: quien editaba
        # en paralelo tiene que enterarse de que su copia quedó vieja.
        nueva_version = await _subir_version(session, shipment_id, row_version)
        await _aplicar_referencias(session, shipment_id, referencias)
        await _registrar_correccion(
            session, shipment_id, sorted(referencias), actor_user_id=actor_user_id
        )
        return nueva_version

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
        nueva_version = int((await session.execute(text(consulta), parametros)).scalar_one())
    except IntegrityError as error:
        raise DatosInvalidos(
            "Alguno de los datos referenciados no existe (ubicación, bodega o responsable)."
        ) from error

    await _aplicar_referencias(session, shipment_id, referencias)
    await _registrar_correccion(
        session,
        shipment_id,
        sorted([*aplicables, *referencias]),
        actor_user_id=actor_user_id,
    )

    return nueva_version


async def _subir_version(session: AsyncSession, shipment_id: UUID, row_version: int) -> int:
    version: int = (
        await session.execute(
            text("""
                UPDATE shipments SET row_version = row_version + 1, updated_at = now()
                WHERE id = :id AND row_version = :version
                RETURNING row_version
            """),
            {"id": shipment_id, "version": row_version},
        )
    ).scalar_one()
    return version


async def _aplicar_referencias(
    session: AsyncSession, shipment_id: UUID, referencias: dict[str, Any]
) -> None:
    """Reemplaza los identificadores que vinieron en el cuerpo.

    Vacío significa borrar: corregir una factura mal tecleada es tan válido como
    ponerle una, y sin esto la equivocada quedaría para siempre. Se borra y se
    reinserta en vez de hacer UPDATE porque una carga puede tener varias del
    mismo tipo y el formulario manda una sola: el reemplazo deja el estado que
    el formulario muestra, que es lo que la persona cree haber guardado.
    """
    for campo, valor in referencias.items():
        tipo = _REFERENCIAS_EDITABLES[campo]
        if tipo == ReferenceType.WR and (valor or "").strip():
            await validar_referencia_permitida(
                session, shipment_id=shipment_id, reference_type=tipo.value
            )
        await session.execute(
            text("""
                DELETE FROM shipment_references
                WHERE shipment_id = :s AND reference_type = :t
            """),
            {"s": shipment_id, "t": tipo.value},
        )

        limpio = (valor or "").strip()
        if limpio:
            await session.execute(
                text("""
                    INSERT INTO shipment_references (shipment_id, reference_type, value)
                    VALUES (:s, :t, :v)
                """),
                {"s": shipment_id, "t": tipo.value, "v": limpio[:120]},
            )


async def _registrar_correccion(
    session: AsyncSession, shipment_id: UUID, campos: list[str], *, actor_user_id: UUID
) -> None:
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
            "descripcion": "Campos actualizados: " + ", ".join(campos),
            "actor": actor_user_id,
        },
    )


async def reemplazar_bultos(
    session: AsyncSession,
    *,
    shipment_id: UUID,
    bultos: tuple[DatosDeBulto, ...],
    row_version: int,
    actor_user_id: UUID,
    permisos: PermisosEfectivos,
) -> int:
    """Cambia el desglose completo de una carga. Devuelve la versión nueva.

    Reemplazo y no parcheo pieza por pieza: el formulario muestra la lista
    entera y quien la guarda cree estar guardando eso. Un `PATCH` por fila
    dejaría al que quitó una pieza en la pantalla con la pieza todavía en la
    base, sin que nada se lo dijera.

    Todo va en la transacción del llamador. El constraint diferible de la base
    comprueba al COMMIT que no quedó vacía, así que un fallo a mitad revierte el
    borrado y la carga conserva su desglose anterior.
    """
    _validar_bultos(bultos)

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
            "Esta carga ya salió de bodega y su desglose no se corrige desde acá."
        )

    if carga.row_version != row_version:
        raise VersionDesactualizada(
            "La carga fue modificada por otra persona. Recárguela y reintente.",
            details=[{"row_version_actual": carga.row_version}],
        )

    await session.execute(
        text("DELETE FROM shipment_packages WHERE shipment_id = :s"), {"s": shipment_id}
    )
    await _guardar_bultos(session, shipment_id, bultos)

    nueva_version = await _subir_version(session, shipment_id, row_version)
    await _registrar_correccion(session, shipment_id, ["piezas"], actor_user_id=actor_user_id)

    return nueva_version


# --- Revisión de lo migrado (ADR-0002 / Paso 5.7) ---


@dataclass(frozen=True)
class CargaEnRevision:
    id: UUID
    shipment_number: str
    company_name: str
    current_status_code: str
    legacy_status: str | None
    motivo: str | None
    created_at: datetime


async def listar_en_revision(
    session: AsyncSession, *, permisos: PermisosEfectivos, limite: int = 100
) -> list[CargaEnRevision]:
    """Cargas que la migración no supo traducir con certeza.

    ADR-0002: cuando el legacy no daba datos para decidir el estado, se marcó en
    vez de inventarlo. Alguien de la operación tiene que mirarlas y decidir; sin
    esta lista quedan como deuda invisible que nadie recuerda.
    """
    if not any(p.code == Perm.SHIPMENTS_LEGACY_REVIEW_RESOLVE for p in permisos.permisos):
        raise SinPermisoParaTransicion(Perm.SHIPMENTS_LEGACY_REVIEW_RESOLVE)

    filas = (
        await session.execute(
            text("""
                SELECT s.id, s.shipment_number, c.legal_name, s.current_status_code,
                       s.legacy_status, s.created_at,
                       (SELECT m.nota FROM legacy_id_map m
                         WHERE m.new_uuid = s.id AND m.new_table = 'shipments'
                         LIMIT 1) AS motivo
                FROM shipments s
                JOIN companies c ON c.id = s.company_id
                WHERE s.legacy_review_required AND s.deleted_at IS NULL
                ORDER BY s.created_at DESC
                LIMIT :limite
            """),
            {"limite": limite},
        )
    ).all()

    return [
        CargaEnRevision(
            id=f.id,
            shipment_number=f.shipment_number,
            company_name=f.legal_name,
            current_status_code=f.current_status_code,
            legacy_status=f.legacy_status,
            motivo=f.motivo,
            created_at=f.created_at,
        )
        for f in filas
    ]


async def resolver_revision(
    session: AsyncSession,
    *,
    shipment_id: UUID,
    nota: str,
    actor_user_id: UUID,
    permisos: PermisosEfectivos,
) -> None:
    """Da por revisada una carga migrada.

    NO cambia el estado: si además hay que corregirlo, eso se hace con una
    transición, que valida el catálogo y deja su propio evento. Acá solo se
    quita la marca, con constancia de quién la revisó y qué concluyó.

    `legacy_status` no se toca nunca (ADR-0002): es el rastro de lo que decía el
    sistema viejo y sigue sirviendo para auditar la traducción años después.
    """
    if not nota.strip():
        raise DatosInvalidos(
            "Indique qué se verificó. Una marca quitada sin explicación no se "
            "puede auditar después."
        )

    carga = (
        await session.execute(
            text("""
                SELECT company_id, legacy_review_required
                FROM shipments WHERE id = :id AND deleted_at IS NULL
                FOR UPDATE
            """),
            {"id": shipment_id},
        )
    ).one_or_none()

    if carga is None:
        raise RecursoNoEncontrado("Carga no encontrada.")

    if not permisos.permite(Perm.SHIPMENTS_LEGACY_REVIEW_RESOLVE, company_id=carga.company_id):
        raise SinPermisoParaTransicion(Perm.SHIPMENTS_LEGACY_REVIEW_RESOLVE)

    if not carga.legacy_review_required:
        raise NoSePuedeEditar("Esta carga no está marcada para revisión.")

    await session.execute(
        text("""
            UPDATE shipments
            SET legacy_review_required = false, updated_at = now()
            WHERE id = :id
        """),
        {"id": shipment_id},
    )

    await session.execute(
        text("""
            INSERT INTO shipment_events
                (shipment_id, event_type, title, description, occurred_at, actor_user_id)
            VALUES (:s, :tipo, :titulo, :descripcion, now(), :actor)
        """),
        {
            "s": shipment_id,
            "tipo": EventType.CORRECTION.value,
            "titulo": "Revisión de migración resuelta",
            "descripcion": nota.strip(),
            "actor": actor_user_id,
        },
    )


# --- Ocultar y recuperar (el "eliminar" del sistema viejo) ---


async def ocultar(
    session: AsyncSession,
    *,
    shipment_id: UUID,
    motivo: str,
    actor_user_id: UUID,
    permisos: PermisosEfectivos,
) -> None:
    """Saca la carga de los listados sin borrarla.

    El sistema viejo la eliminaba de verdad. Acá no: sus documentos, su línea de
    tiempo y su auditoría siguen existiendo y apuntando a ella, y borrarla
    dejaría todo eso huérfano (ADR-0007). Se puede recuperar.

    El motivo es obligatorio: una carga que desaparece sin explicación es
    indistinguible de una que se perdió.
    """
    if not motivo.strip():
        raise DatosInvalidos("Indique por qué se oculta esta carga.")

    carga = (
        await session.execute(
            text("""
                SELECT company_id, hidden_at FROM shipments
                WHERE id = :id AND deleted_at IS NULL
                FOR UPDATE
            """),
            {"id": shipment_id},
        )
    ).one_or_none()

    if carga is None:
        raise RecursoNoEncontrado("Carga no encontrada.")

    if not permisos.permite(Perm.SHIPMENTS_UPDATE, company_id=carga.company_id):
        raise SinPermisoParaTransicion(Perm.SHIPMENTS_UPDATE)

    if carga.hidden_at is not None:
        raise NoSePuedeEditar("Esta carga ya está oculta.")

    await session.execute(
        text("""
            UPDATE shipments
            SET hidden_at = now(), hidden_by = :actor, hidden_reason = :motivo,
                row_version = row_version + 1, updated_at = now()
            WHERE id = :id
        """),
        {"actor": actor_user_id, "motivo": motivo.strip(), "id": shipment_id},
    )

    await session.execute(
        text("""
            INSERT INTO shipment_events
                (shipment_id, event_type, title, description, occurred_at, actor_user_id)
            VALUES (:s, :tipo, :titulo, :descripcion, now(), :actor)
        """),
        {
            "s": shipment_id,
            "tipo": EventType.NOTE.value,
            "titulo": "Carga ocultada",
            "descripcion": motivo.strip(),
            "actor": actor_user_id,
        },
    )


async def recuperar(
    session: AsyncSession,
    *,
    shipment_id: UUID,
    actor_user_id: UUID,
    permisos: PermisosEfectivos,
) -> None:
    """Devuelve una carga oculta a los listados."""
    carga = (
        await session.execute(
            text("""
                SELECT company_id, hidden_at FROM shipments
                WHERE id = :id AND deleted_at IS NULL
                FOR UPDATE
            """),
            {"id": shipment_id},
        )
    ).one_or_none()

    if carga is None:
        raise RecursoNoEncontrado("Carga no encontrada.")

    if not permisos.permite(Perm.SHIPMENTS_UPDATE, company_id=carga.company_id):
        raise SinPermisoParaTransicion(Perm.SHIPMENTS_UPDATE)

    if carga.hidden_at is None:
        raise NoSePuedeEditar("Esta carga no está oculta.")

    await session.execute(
        text("""
            UPDATE shipments
            SET hidden_at = NULL, hidden_by = NULL, hidden_reason = NULL,
                row_version = row_version + 1, updated_at = now()
            WHERE id = :id
        """),
        {"id": shipment_id},
    )

    await session.execute(
        text("""
            INSERT INTO shipment_events
                (shipment_id, event_type, title, occurred_at, actor_user_id)
            VALUES (:s, :tipo, :titulo, now(), :actor)
        """),
        {
            "s": shipment_id,
            "tipo": EventType.NOTE.value,
            "titulo": "Carga recuperada",
            "actor": actor_user_id,
        },
    )
