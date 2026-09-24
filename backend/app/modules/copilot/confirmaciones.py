"""Ejecutores de CONFIRMACIÓN de herramientas de ESCRITURA (ADR-0012, Fase 4).

Un ejecutor de este módulo corre cuando la persona confirma una propuesta
(`POST /copilot/proposals/{id}/confirm`) — nunca durante el turno del modelo.
Es acá, y solo acá, donde una propuesta de AMVI puede terminar tocando datos
de dominio, y siempre a través del mismo service/command que usa el resto de
la API (`shipments.gestion.crear` o `.actualizar`) — nunca con un `INSERT`/
`UPDATE` propio: dos caminos hacia el mismo efecto de dominio solo pueden
divergir con el tiempo, y esos commands ya traen su propia revalidación de
permiso, así que confirmar por acá pasa por el mismo control que un PATCH o
un alta a mano.

`router.confirmar_propuesta` importa este módulo por su efecto de registrar
`REGISTRO_DE_CONFIRMACION` (una entrada por `AccionCopilot`) — igual que
`service.py` arma `REGISTRO_EJECUTORES` a partir de `executors_escritura.py`.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import SinPermiso
from app.modules.audit.service import registrar
from app.modules.copilot import propuestas
from app.modules.copilot.acciones import REGISTRO_DE_CONFIRMACION, AccionCopilot
from app.modules.dispatches import service as despachos
from app.modules.rbac.catalog import Perm
from app.modules.rbac.service import PermisosEfectivos
from app.modules.shipments import queries as shipments_queries
from app.modules.shipments import service as shipments_service
from app.modules.shipments.gestion import (
    DatosDeBulto,
    DatosDeCarga,
    DatosInvalidos,
    actualizar,
    crear,
)


async def confirmar_crear_prealerta_borrador(
    session: AsyncSession,
    permisos: PermisosEfectivos,
    propuesta_id: UUID,
    campos: dict[str, Any],
) -> dict[str, Any]:
    """`campos` es lo que la persona corrigió antes de confirmar — pisa el
    borrador (`propuesta.payload`), nunca al revés. `company_id` sale
    SIEMPRE de la propuesta ya persistida (que a su vez salió del JWT al
    crearla), nunca de `campos`: la persona puede corregir la descripción o
    el peso, no la empresa a la que pertenece la carga.
    """
    propuesta = await propuestas.obtener(session, propuesta_id)
    datos = dict(propuesta.payload)
    correcciones = {c: v for c, v in campos.items() if c != "company_id"}

    # El borrador guarda el ID ya resuelto (`origen_location_id`), pero lo que
    # la persona ve y puede corregir es el CÓDIGO (`origen_location_code`, el
    # mismo campo que armó `_campo_ubicacion`) — sin este puente, corregir un
    # origen que AMVI no encontró al proponer no tendría ningún efecto real.
    for lado, clave_id in (("origen", "origen_location_id"), ("destino", "destino_location_id")):
        codigo = correcciones.pop(f"{lado}_location_code", None)
        if codigo:
            ubicacion = await shipments_queries.ubicacion_por_codigo(session, codigo)
            if ubicacion is None:
                raise DatosInvalidos(f"No se encontró la ubicación «{codigo}».")
            datos[clave_id] = str(ubicacion.id)

    datos.update(correcciones)

    origen_id = datos.get("origen_location_id")
    destino_id = datos.get("destino_location_id")
    bulto_tipo = datos.get("bulto_tipo")
    bulto_cantidad = datos.get("bulto_cantidad")
    if not origen_id or not destino_id:
        raise DatosInvalidos("Hace falta origen y destino para crear la carga.")
    if not bulto_tipo or not bulto_cantidad:
        raise DatosInvalidos("Hace falta indicar al menos una pieza (tipo y cantidad).")

    creada = await crear(
        session,
        datos=DatosDeCarga(
            company_id=propuesta.company_id,
            origin_location_id=UUID(str(origen_id)),
            destination_location_id=UUID(str(destino_id)),
            description=datos.get("descripcion"),
            invoice=datos.get("factura"),
            weight_kg=Decimal(str(datos["peso_kg"])) if datos.get("peso_kg") else None,
            packages=(DatosDeBulto(package_type=str(bulto_tipo), quantity=int(bulto_cantidad)),),
        ),
        actor_user_id=propuesta.created_by,
        permisos=permisos,
    )

    return {
        "shipment_id": str(creada.id),
        "shipment_number": creada.shipment_number,
        "estado": creada.status,
    }


async def confirmar_procesar_factura_ocr(
    session: AsyncSession,
    permisos: PermisosEfectivos,
    propuesta_id: UUID,
    campos: dict[str, Any],
) -> dict[str, Any]:
    """Actualiza la carga DUEÑA de la factura — nunca crea una carga nueva
    (a diferencia de `confirmar_crear_prealerta_borrador`, acá siempre parte
    de un documento que ya pertenece a una carga existente). Reusa
    `shipments.gestion.actualizar`, el mismo command que usa el PATCH normal:
    revalida `SHIPMENTS_UPDATE`, el estado editable de la carga y la versión
    optimista, todo de nuevo acá — no solo al proponer.
    """
    propuesta = await propuestas.obtener(session, propuesta_id)
    datos = dict(propuesta.payload)
    # `shipment_id` y `row_version` salen SIEMPRE de la propuesta ya
    # persistida, nunca de `campos`: la persona corrige el número de
    # factura, no a qué carga ni sobre qué versión se aplica.
    datos.update(
        {c: v for c, v in campos.items() if c not in ("company_id", "shipment_id", "row_version")}
    )

    factura = datos.get("factura")
    if not factura:
        raise DatosInvalidos("No hay ningún número de factura para guardar.")

    nueva_version = await actualizar(
        session,
        shipment_id=UUID(str(datos["shipment_id"])),
        cambios={"invoice": factura},
        row_version=int(datos["row_version"]),
        actor_user_id=propuesta.created_by,
        permisos=permisos,
    )

    return {"shipment_id": str(datos["shipment_id"]), "row_version": nueva_version}


async def confirmar_proponer_cambio_estado(
    session: AsyncSession,
    permisos: PermisosEfectivos,
    propuesta_id: UUID,
    campos: dict[str, Any],
) -> dict[str, Any]:
    """Aplica los pasos con `shipments.service.transicionar`, el mismo motor que
    `POST /shipments/{id}/transitions`: permiso, bloqueos, fechas, línea de
    tiempo y notificaciones se revalidan acá, paso por paso.

    Nada es editable: `campos` se ignora. La carga, la versión y los pasos
    salen de la propuesta persistida. Si la carga cambió desde que se propuso,
    el primer paso choca con la versión y no se aplica ninguno (el router hace
    rollback y marca la propuesta FAILED).
    """
    propuesta = await propuestas.obtener(session, propuesta_id)
    datos = dict(propuesta.payload)
    shipment_id = UUID(str(datos["shipment_id"]))
    version = int(datos["row_version"])
    estado = ""

    for paso in datos["pasos"]:
        try:
            resultado = await shipments_service.transicionar(
                session,
                shipment_id=shipment_id,
                datos=shipments_service.DatosTransicion(
                    to_status=str(paso), row_version=version, note="Confirmado desde AMVI."
                ),
                actor_user_id=propuesta.created_by,
                permisos=permisos,
            )
        except shipments_service.SinPermisoParaTransicion as error:
            # No hereda de `ErrorDeAplicacion`: sin traducirla, el router no la
            # atraparía y la propuesta quedaría PENDING con un 500.
            raise SinPermiso("Ya no tenés permiso para cambiar el estado de esta carga.") from error
        version = resultado.row_version
        estado = resultado.hacia

    return {
        "shipment_id": str(shipment_id),
        "shipment_number": datos.get("shipment_number"),
        "estado": estado,
        "row_version": version,
    }


async def confirmar_proponer_despacho(
    session: AsyncSession,
    permisos: PermisosEfectivos,
    propuesta_id: UUID,
    campos: dict[str, Any],
) -> dict[str, Any]:
    """Crea la solicitud con `dispatches.service.crear`, el mismo que usa
    `POST /dispatches`: estado de las cargas, reclamo exclusivo, línea de
    tiempo y avisos (acuse al cliente, pedido a Operaciones) salen de ahí.

    La empresa y las cargas salen siempre de la propuesta persistida; la
    persona corrige método, dirección, instrucciones o fecha, nada más.
    """
    propuesta = await propuestas.obtener(session, propuesta_id)
    datos = dict(propuesta.payload)
    editables = {
        "metodo": "method",
        "direccion_entrega": "delivery_address",
        "instrucciones": "instructions",
        "fecha_retiro": "requested_pickup_date",
    }
    for campo, clave in editables.items():
        if campo in campos:
            datos[clave] = (
                (str(campos[campo]).strip() or None) if campos[campo] is not None else None
            )

    metodo = str(datos.get("method") or "").upper()
    if metodo not in {"SEA", "AIR", "LAND"}:
        raise DatosInvalidos("El método tiene que ser SEA, AIR o LAND.")
    fecha: date | None = None
    if datos.get("requested_pickup_date"):
        try:
            fecha = date.fromisoformat(str(datos["requested_pickup_date"]))
        except ValueError as error:
            raise DatosInvalidos("La fecha de retiro tiene que ser AAAA-MM-DD.") from error

    empresa = propuesta.company_id
    # `POST /dispatches` exige este permiso en su router, no en el servicio: se
    # revalida acá con la empresa de la propuesta, como hace ese router.
    if empresa is None or not permisos.permite(Perm.DISPATCH_REQUESTS_CREATE, company_id=empresa):
        raise SinPermiso("Ya no tenés permiso para solicitar despachos de esta empresa.")

    try:
        solicitud = await despachos.crear(
            session,
            company_id=empresa,
            actor_user_id=propuesta.created_by,
            method=metodo,
            shipment_ids=[UUID(str(i)) for i in datos["shipment_ids"]],
            permisos=permisos,
            delivery_address=datos.get("delivery_address"),
            instructions=datos.get("instructions"),
            requested_pickup_date=fecha,
        )
    except despachos.SinPermisoParaDespacho as error:
        raise SinPermiso("Ya no tenés permiso para solicitar este despacho.") from error

    await registrar(
        session,
        action="dispatch.created",
        resource_type="dispatch_request",
        resource_id=solicitud.id,
        actor_user_id=propuesta.created_by,
        company_id=empresa,
        after_data={
            "dispatch_number": solicitud.dispatch_number,
            "cargas": len(solicitud.shipment_ids),
            "method": metodo,
            "origen": "copilot",
        },
    )

    return {
        "dispatch_id": str(solicitud.id),
        "dispatch_number": solicitud.dispatch_number,
        "estado": solicitud.status,
    }


REGISTRO_DE_CONFIRMACION[AccionCopilot.PROPONER_DESPACHO] = confirmar_proponer_despacho
REGISTRO_DE_CONFIRMACION[AccionCopilot.PROPONER_CAMBIO_ESTADO] = confirmar_proponer_cambio_estado
REGISTRO_DE_CONFIRMACION[AccionCopilot.CREAR_PREALERTA_BORRADOR] = (
    confirmar_crear_prealerta_borrador
)
REGISTRO_DE_CONFIRMACION[AccionCopilot.PROCESAR_FACTURA_OCR] = confirmar_procesar_factura_ocr
