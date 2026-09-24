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

from sqlalchemy import text
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
from app.modules.shipments import service as shipments_service
from app.modules.shipments.catalog import ESTADOS
from app.modules.shipments.models import ShipmentStatus

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


async def _empresas_por_nombre(session: AsyncSession, nombre: str) -> list[Any]:
    """Empresas activas cuyo nombre legal o comercial contiene `nombre`.

    Devuelve la lista y no una sola: si el nombre es ambiguo, quien decide es
    la persona, no el modelo. Dos nombres parecidos ("Extreme Tech" y "Extreme
    Tech CR") son justo el caso donde adivinar mal manda la carga al
    expediente del cliente equivocado.
    """
    filas = await session.execute(
        text("""
            SELECT id, legal_name, trade_name
            FROM companies
            WHERE deleted_at IS NULL AND status = 'ACTIVE'
              AND (legal_name ILIKE :patron OR trade_name ILIKE :patron)
            ORDER BY legal_name
            LIMIT 6
        """),
        {"patron": f"%{nombre.strip()}%"},
    )
    return list(filas.all())


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
    # Quién es el dueño de la carga. Un cliente lo trae del JWT y no puede
    # elegir otro; el personal de AMVARMAR —que es quien registra desde
    # ADR-0017— no tiene empresa propia y lo dice en la conversación.
    if company_id is None:
        nombre_empresa = (argumentos.get("empresa") or "").strip()
        if not nombre_empresa:
            return {
                "error": (
                    "Falta de qué cliente es la carga. Decime el nombre de la "
                    "empresa y preparo el borrador."
                )
            }

        candidatas = await _empresas_por_nombre(session, nombre_empresa)
        if not candidatas:
            return {"error": f"No encontré ninguna empresa activa que se llame «{nombre_empresa}»."}
        if len(candidatas) > 1:
            nombres = ", ".join(f"«{c.trade_name or c.legal_name}»" for c in candidatas)
            return {
                "error": (
                    f"«{nombre_empresa}» coincide con varias empresas: {nombres}. "
                    "Decime cuál es para no registrar la carga en el expediente equivocado."
                )
            }
        company_id = candidatas[0].id

    # Revalidación con la empresa ya resuelta (ADR-0012): el filtro que decidió
    # ofrecer esta herramienta miró los permisos del actor sin saber sobre qué
    # empresa iba a operar. Un alcance ORGANIZATION que apunte a otra empresa
    # tiene que caer acá, no al confirmar.
    if not permisos.permite(Perm.SHIPMENTS_CREATE, company_id=company_id):
        return {"error": "No tenés permiso para registrar cargas de esa empresa."}

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

    alcance_empresas = tuple(
        permiso.company_id
        for permiso in permisos.permisos
        if permiso.code == Perm.DOCUMENTS_UPLOAD_INTERNAL
        and permiso.scope_type == "ORGANIZATION"
        and permiso.company_id is not None
    )
    alcance_global = any(
        permiso.code == Perm.DOCUMENTS_UPLOAD_INTERNAL and permiso.scope_type == "GLOBAL"
        for permiso in permisos.permisos
    )

    documento = await documents_queries.factura_de_carga(
        session,
        UUID(str(argumentos["document_id"])),
        company_ids=None if alcance_global else alcance_empresas,
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


# Ingreso a bodega, en orden. Son los avances que no piden justificación
# (ADR-0001); lo que sigue a STORED lo mueve el flujo de despachos.
_INGRESO_A_BODEGA: tuple[str, ...] = (
    ShipmentStatus.PRE_ALERT,
    ShipmentStatus.IN_TRANSIT,
    ShipmentStatus.RECEIVED,
    ShipmentStatus.STORED,
)


async def _cargas_por_referencia_exacta(
    session: AsyncSession, permisos: PermisosEfectivos, referencia: str
) -> list[Any]:
    """Número de carga, factura o ID, con coincidencia EXACTA.

    No reusa la búsqueda del listado: es parcial (ILIKE) y además mira shipper
    y carrier, así que "SHP-2026" encontraría cualquier carga del año. Para
    escribir, la carga tiene que quedar identificada sin margen de duda. El
    alcance se aplica igual que en el resto: se descartan las cargas que el
    actor no puede leer.
    """
    filas = (
        await session.execute(
            text("""
                SELECT s.id, s.shipment_number, s.company_id, s.current_status_code,
                       s.row_version, c.legal_name AS company_name
                FROM shipments s
                JOIN companies c ON c.id = s.company_id
                WHERE s.deleted_at IS NULL
                  AND (
                    lower(s.shipment_number) = lower(:ref)
                    OR CAST(s.id AS text) = lower(:ref)
                    OR EXISTS (
                        SELECT 1 FROM shipment_references r
                        WHERE r.shipment_id = s.id AND r.reference_type = 'INVOICE'
                          AND lower(trim(r.value)) = lower(:ref)
                    )
                  )
                ORDER BY s.created_at DESC
                LIMIT 6
            """),
            {"ref": referencia.strip()},
        )
    ).all()
    return [f for f in filas if permisos.permite(Perm.SHIPMENTS_READ, company_id=f.company_id)]


def _etiqueta(estado: str) -> str:
    definicion = ESTADOS.get(estado)
    return definicion.label if definicion else estado


async def proponer_cambio_estado(
    session: AsyncSession,
    permisos: PermisosEfectivos,
    actor_user_id: UUID,
    company_id: UUID | None,
    argumentos: dict[str, Any],
) -> dict[str, Any]:
    """Arma la propuesta de avanzar una carga en el ingreso a bodega.

    Solo persiste la propuesta; el estado cambia al confirmar
    (`confirmaciones.confirmar_proponer_cambio_estado`). Si la carga no se
    identifica sin ambigüedad, o el primer paso está bloqueado, no propone
    nada y devuelve el motivo para que AMVI lo explique.
    """
    referencia = str(argumentos["carga"])
    destino = str(argumentos["estado_destino"])

    candidatas = await _cargas_por_referencia_exacta(session, permisos, referencia)
    if not candidatas:
        return {
            "encontrada": False,
            "mensaje": f"No hay ninguna carga con número, factura o ID «{referencia}».",
        }
    if len(candidatas) > 1:
        return {
            "encontrada": True,
            "ambigua": True,
            "mensaje": "Varias cargas coinciden; hay que elegir una por su número.",
            "candidatas": [
                {
                    "numero": f.shipment_number,
                    "estado": _etiqueta(f.current_status_code),
                    "empresa": f.company_name,
                }
                for f in candidatas
            ],
        }

    carga = candidatas[0]
    actual = carga.current_status_code

    def sin_propuesta(motivo: str) -> dict[str, Any]:
        return {
            "encontrada": True,
            "carga": carga.shipment_number,
            "estado_actual": _etiqueta(actual),
            "propuesta": None,
            "motivo": motivo,
        }

    if actual not in _INGRESO_A_BODEGA[:-1] or destino not in _INGRESO_A_BODEGA:
        return sin_propuesta(
            f"La carga está en «{_etiqueta(actual)}». AMVI solo avanza el ingreso a "
            "bodega (Prealerta, En tránsito, Recibida, Almacenada); lo demás se hace "
            "desde la carga."
        )
    desde_i = _INGRESO_A_BODEGA.index(actual)
    hasta_i = _INGRESO_A_BODEGA.index(destino)
    if hasta_i <= desde_i:
        return sin_propuesta(
            f"La carga ya está en «{_etiqueta(actual)}». Retroceder pide una "
            "justificación y se hace desde la carga, no desde AMVI."
        )
    pasos = list(_INGRESO_A_BODEGA[desde_i + 1 : hasta_i + 1])

    # El primer paso se valida ya, con el mismo cálculo que la pantalla de
    # transiciones. Los siguientes dependen de lo que abra el primero (por
    # ejemplo, requisitos al entrar a RECEIVED): se revalidan al confirmar.
    disponibles = await shipments_service.transiciones_disponibles(
        session, shipment_id=carga.id, permisos=permisos
    )
    primero = next((d for d in disponibles if d.to_status == pasos[0]), None)
    if primero is None:
        return sin_propuesta(f"Tu cuenta no puede pasar esta carga a «{_etiqueta(pasos[0])}».")
    if primero.blocked:
        faltantes = "; ".join(str(b.get("message", "")) for b in primero.blockers)
        return sin_propuesta(f"No se puede pasar a «{_etiqueta(pasos[0])}»: {faltantes}")

    recorrido = " → ".join(_etiqueta(e) for e in (actual, *pasos))
    campos = [
        CampoPropuesto(
            nombre="carga",
            etiqueta="Carga",
            valor=carga.shipment_number,
            confianza=1.0,
            editable=False,
        ),
        CampoPropuesto(
            nombre="empresa",
            etiqueta="Empresa",
            valor=carga.company_name,
            confianza=1.0,
            editable=False,
        ),
        CampoPropuesto(
            nombre="cambio",
            etiqueta="Cambio de estado",
            valor=recorrido,
            confianza=1.0,
            editable=False,
        ),
    ]
    advertencias = (
        ["Cada paso se revalida al confirmar; si alguno se bloquea, no se aplica ninguno."]
        if len(pasos) > 1
        else []
    )

    expira_en = datetime.now(UTC) + timedelta(minutes=get_settings().copilot_propuesta_ttl_minutos)
    propuesta_id = await propuestas.crear(
        session,
        creador=actor_user_id,
        company_id=carga.company_id,
        action_code=AccionCopilot.PROPONER_CAMBIO_ESTADO,
        payload={
            "shipment_id": str(carga.id),
            "shipment_number": carga.shipment_number,
            "row_version": carga.row_version,
            "pasos": pasos,
        },
        expira_en=expira_en,
    )

    propuesta = PropuestaAccion(
        id=str(propuesta_id),
        action_code=AccionCopilot.PROPONER_CAMBIO_ESTADO,
        titulo="Cambio de estado",
        resumen_efecto=f"{carga.shipment_number}: {recorrido}.",
        expira_en=expira_en.isoformat(),
        campos=campos,
        advertencias=advertencias,
    )
    return propuesta.model_dump(mode="json")


REGISTRO_EJECUTORES_ESCRITURA: dict[str, EjecutorHerramienta] = {
    "proponer_cambio_estado": proponer_cambio_estado,
    "crear_prealerta_borrador": crear_prealerta_borrador,
    "procesar_factura_ocr": procesar_factura_ocr,
}
