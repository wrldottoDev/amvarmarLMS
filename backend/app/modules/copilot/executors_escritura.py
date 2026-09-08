"""Ejecutores de PREVIEW de herramientas de ESCRITURA (ADR-0012, Fase 4).

Un ejecutor de este módulo corre durante el turno del modelo, con la misma
firma que uno de lectura (`executors.EjecutorHerramienta`) — pero la ÚNICA
escritura que hace es persistir una `PropuestaAccion` en
`copilot_action_proposals` (`propuestas.crear`), con estado `PENDING`. Nunca
toca una tabla de dominio: eso ocurre en `confirmaciones.py`, después de que
la persona revisa y confirma por `POST /copilot/proposals/{id}/confirm`.

Separado de `executors.py` a propósito: mezclar los dos catálogos en un solo
lugar oscurecería justamente la línea que el ADR pide mantener (ninguna
escritura de dominio durante el turno del modelo).
"""

from __future__ import annotations

import base64
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.infrastructure.storage import s3
from app.modules.copilot import propuestas
from app.modules.copilot.acciones import AccionCopilot
from app.modules.copilot.executors import EjecutorHerramienta
from app.modules.copilot.provider import proveedor_actual
from app.modules.copilot.tools import CampoPropuesto, PropuestaAccion
from app.modules.documents import queries as documents_queries
from app.modules.documents.models import UploadStatus
from app.modules.rbac.catalog import Perm
from app.modules.rbac.service import PermisosEfectivos
from app.modules.shipments import queries as shipments_queries

# Base64 infla ~33%; esto acota el tamaño del payload que se manda al
# proveedor en una sola llamada (ADR-0012, Fase 6) — muy por debajo del tope
# de subida (`documents.service.LIMITES_POR_DEFECTO`, hasta 250 MB), que
# gobierna qué se puede GUARDAR, no qué es razonable mandarle a un modelo.
_LIMITE_OCR_BYTES = 15 * 1024 * 1024


def _campo_texto(nombre: str, etiqueta: str, valor: str | None) -> CampoPropuesto:
    return CampoPropuesto(
        nombre=nombre, etiqueta=etiqueta, valor=valor, confianza=1.0 if valor else 0.0
    )


def _campo_numero(nombre: str, etiqueta: str, valor: float | int | None) -> CampoPropuesto:
    return CampoPropuesto(
        nombre=nombre, etiqueta=etiqueta, valor=valor, confianza=1.0 if valor else 0.0
    )


async def _resolver_ubicacion(session: AsyncSession, codigo: str | None) -> Any | None:
    if not codigo:
        return None
    return await shipments_queries.ubicacion_por_codigo(session, codigo)


def _campo_ubicacion(
    nombre: str, etiqueta: str, codigo: str | None, fila: Any | None
) -> CampoPropuesto:
    if fila is not None:
        return CampoPropuesto(nombre=nombre, etiqueta=etiqueta, valor=fila.name, confianza=1.0)
    if codigo:
        # Lo dijeron, pero no matchea ningún puerto/ciudad activo: se muestra
        # tal cual con confianza baja, no se descarta en silencio.
        return CampoPropuesto(nombre=nombre, etiqueta=etiqueta, valor=codigo, confianza=0.3)
    return CampoPropuesto(nombre=nombre, etiqueta=etiqueta, valor=None, confianza=0.0)


async def crear_prealerta_borrador(
    session: AsyncSession,
    permisos: PermisosEfectivos,
    actor_user_id: UUID,
    company_id: UUID | None,
    argumentos: dict[str, Any],
) -> dict[str, Any]:
    """Arma el borrador y lo persiste como propuesta `PENDING`. No crea la
    carga — eso lo hace `confirmaciones.confirmar_crear_prealerta_borrador`,
    después de que la persona revisa y confirma."""
    if company_id is None:
        # El alcance sale del JWT (ADR-0012): personal interno sin una única
        # empresa propia no tiene un `company_id` que asignarle al borrador,
        # y el modelo no puede elegir uno. Preparar prealertas para un
        # cliente específico desde el chat de Operaciones queda pendiente de
        # diseño, no es una limitación de esta herramienta puntual.
        return {
            "error": (
                "Por ahora AMVI solo puede preparar prealertas para cuentas de "
                "cliente, no para personal interno."
            )
        }

    origen_codigo = argumentos.get("origen_location_code")
    destino_codigo = argumentos.get("destino_location_code")
    origen = await _resolver_ubicacion(session, origen_codigo)
    destino = await _resolver_ubicacion(session, destino_codigo)
    factura = argumentos.get("factura")
    peso_kg = argumentos.get("peso_kg")
    bulto_tipo = argumentos.get("bulto_tipo")
    bulto_cantidad = argumentos.get("bulto_cantidad")

    advertencias: list[str] = []
    if origen_codigo and origen is None:
        advertencias.append(
            f"No encontré el origen «{origen_codigo}»; hay que elegirlo al confirmar."
        )
    if destino_codigo and destino is None:
        advertencias.append(
            f"No encontré el destino «{destino_codigo}»; hay que elegirlo al confirmar."
        )
    if origen is None:
        advertencias.append("Falta el origen: es obligatorio para crear la carga.")
    if destino is None:
        advertencias.append("Falta el destino: es obligatorio para crear la carga.")
    if not factura:
        advertencias.append(
            "Sin factura, la carga solo puede identificarse si sale de una bodega con "
            "Warehouse Receipt."
        )
    if not peso_kg:
        advertencias.append("Falta el peso: es obligatorio para crear la carga.")
    if not bulto_tipo or not bulto_cantidad:
        advertencias.append(
            "Falta el tipo y la cantidad de bultos: toda carga necesita al menos una pieza."
        )

    campos = [
        _campo_texto("descripcion", "Descripción", argumentos.get("descripcion")),
        _campo_ubicacion("origen_location_code", "Origen", origen_codigo, origen),
        _campo_ubicacion("destino_location_code", "Destino", destino_codigo, destino),
        _campo_texto("factura", "Factura", factura),
        _campo_numero("peso_kg", "Peso (kg)", peso_kg),
        _campo_texto("bulto_tipo", "Tipo de bulto", bulto_tipo),
        _campo_numero("bulto_cantidad", "Cantidad de bultos", bulto_cantidad),
    ]

    payload = {
        "descripcion": argumentos.get("descripcion"),
        "origen_location_id": str(origen.id) if origen else None,
        "destino_location_id": str(destino.id) if destino else None,
        "factura": factura,
        "peso_kg": peso_kg,
        "bulto_tipo": bulto_tipo,
        "bulto_cantidad": bulto_cantidad,
    }

    expira_en = datetime.now(UTC) + timedelta(minutes=get_settings().copilot_propuesta_ttl_minutos)
    propuesta_id = await propuestas.crear(
        session,
        creador=actor_user_id,
        company_id=company_id,
        action_code=AccionCopilot.CREAR_PREALERTA_BORRADOR,
        payload=payload,
        expira_en=expira_en,
    )

    propuesta = PropuestaAccion(
        id=str(propuesta_id),
        action_code=AccionCopilot.CREAR_PREALERTA_BORRADOR,
        titulo="Borrador de prealerta",
        resumen_efecto="Crea una carga nueva en estado de prealerta con estos datos.",
        expira_en=expira_en.isoformat(),
        campos=campos,
        advertencias=advertencias,
    )
    return propuesta.model_dump(mode="json")


async def procesar_factura_ocr(
    session: AsyncSession,
    permisos: PermisosEfectivos,
    actor_user_id: UUID,
    company_id: UUID | None,  # sin usar: el alcance sale del documento, no del actor
    argumentos: dict[str, Any],
) -> dict[str, Any]:
    """Lee una factura YA subida — todo documento se sube ya atado a una
    carga (`documents.service.preparar_subida` exige `shipment_id` desde el
    primer tiempo, ver `documents/queries.py::factura_de_carga`) — y arma un
    borrador para corregir ESA carga. Nunca crea una carga nueva: a
    diferencia de `crear_prealerta_borrador`, acá siempre hay una carga
    dueña del documento desde el principio.

    Solo `numero_guia` tiene un destino claro en el modelo de datos
    (`shipment_references`, tipo INVOICE, ver `shipments/gestion.py`).
    Proveedor, monto y cliente se muestran como referencia para que la
    persona los verifique a mano — no hay una columna de `shipments` a la
    que mapearlos sin inventar una regla que el negocio no definió.
    """
    # Mismo mensaje para "no existe" y "existe pero es de otra empresa"
    # (igual que `router._propuesta_del_actor`: "ajena o inexistente
    # responden igual") — separarlos dejaría confirmar si un document_id de
    # otra empresa existe, un oráculo de existencia cross-empresa.
    sin_acceso = {"error": "No encontré ese documento, o no pertenece a ninguna carga."}

    documento = await documents_queries.factura_de_carga(
        session, UUID(str(argumentos["document_id"]))
    )
    if documento is None:
        return sin_acceso

    if not permisos.permite(Perm.DOCUMENTS_UPLOAD_INTERNAL, company_id=documento.company_id):
        return sin_acceso

    if documento.upload_status != UploadStatus.READY:
        return {"error": "Ese documento todavía no terminó de procesarse."}

    try:
        tamano = (await s3.describir_objeto(documento.storage_key)).size_bytes
    except s3.ObjetoNoEncontrado:
        return {"error": "El archivo del documento ya no está disponible."}
    if tamano > _LIMITE_OCR_BYTES:
        return {"error": "El documento es demasiado grande para que AMVI lo lea."}

    contenido = bytearray()
    async for fragmento in s3.iterar_chunks(documento.storage_key):
        contenido.extend(fragmento)

    descripcion = await proveedor_actual.get().describir_factura(
        media_type=documento.media_type,
        contenido_base64=base64.b64encode(bytes(contenido)).decode(),
    )

    advertencias: list[str] = []
    if not descripcion.numero_guia:
        advertencias.append("No pude leer el número de factura de este documento.")
    if not descripcion.proveedor:
        advertencias.append("No pude leer el proveedor de este documento.")
    if descripcion.monto is None:
        advertencias.append("No pude leer el monto de este documento.")
    if not descripcion.cliente:
        advertencias.append("No pude leer el cliente de este documento.")

    monto_texto = (
        f"{descripcion.monto} {descripcion.moneda or ''}".strip()
        if descripcion.monto is not None
        else None
    )

    campos = [
        CampoPropuesto(
            nombre="carga",
            etiqueta="Carga",
            valor=documento.shipment_number,
            confianza=1.0,
            editable=False,
        ),
        _campo_texto("factura", "Número de factura", descripcion.numero_guia),
        CampoPropuesto(
            nombre="proveedor_detectado",
            etiqueta="Proveedor (referencia, no se guarda)",
            valor=descripcion.proveedor,
            confianza=1.0 if descripcion.proveedor else 0.0,
            editable=False,
        ),
        CampoPropuesto(
            nombre="monto_detectado",
            etiqueta="Monto (referencia, no se guarda)",
            valor=monto_texto,
            confianza=1.0 if monto_texto else 0.0,
            editable=False,
        ),
        CampoPropuesto(
            nombre="cliente_detectado",
            etiqueta="Cliente (referencia, no se guarda)",
            valor=descripcion.cliente,
            confianza=1.0 if descripcion.cliente else 0.0,
            editable=False,
        ),
    ]

    payload = {
        "shipment_id": str(documento.shipment_id),
        "row_version": documento.row_version,
        "factura": descripcion.numero_guia,
    }

    expira_en = datetime.now(UTC) + timedelta(minutes=get_settings().copilot_propuesta_ttl_minutos)
    propuesta_id = await propuestas.crear(
        session,
        creador=actor_user_id,
        company_id=documento.company_id,
        action_code=AccionCopilot.PROCESAR_FACTURA_OCR,
        payload=payload,
        expira_en=expira_en,
    )

    propuesta = PropuestaAccion(
        id=str(propuesta_id),
        action_code=AccionCopilot.PROCESAR_FACTURA_OCR,
        titulo="Datos leídos de la factura",
        resumen_efecto=f"Actualiza el número de factura de {documento.shipment_number}.",
        expira_en=expira_en.isoformat(),
        campos=campos,
        advertencias=advertencias,
    )
    return propuesta.model_dump(mode="json")


REGISTRO_EJECUTORES_ESCRITURA: dict[str, EjecutorHerramienta] = {
    "crear_prealerta_borrador": crear_prealerta_borrador,
    "procesar_factura_ocr": procesar_factura_ocr,
}
