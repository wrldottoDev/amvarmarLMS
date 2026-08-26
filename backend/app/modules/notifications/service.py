"""Notificaciones: creación, entrega por canal y bandeja (Paso 4.2).

El flujo completo: un cambio de negocio publica un evento en el outbox (Paso
4.1); el worker lo toma y llama a `notificar()`, que crea una fila por
destinatario y una entrega por canal. `IN_APP` queda entregada en el acto —
crear la fila ES la entrega. `EMAIL` sale por el relay.

Idempotencia: el outbox garantiza *al menos una vez* (ADR-0014), así que todo
esto puede ejecutarse dos veces con el mismo evento. La defensa es el índice
único de `notification_deliveries(notification_id, channel)` más el `dedup_key`
de la notificación: reprocesar no genera un segundo correo.
"""

import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.logging import obtener_logger
from app.core.observability import metrics
from app.core.pagination import Cursor, Pagina, armar_pagina
from app.core.security.token_fingerprint import fingerprint
from app.modules.notifications import email as correo
from app.modules.notifications.catalog import DefinicionEvento, definicion
from app.modules.notifications.catalog import texto as texto_del_evento
from app.modules.notifications.models import Channel, DeliveryStatus

_log = obtener_logger("notificaciones")


class EventoDesconocido(Exception):
    """El código de evento no está en el catálogo de ADR-0008."""


@dataclass(frozen=True)
class Destinatario:
    user_id: UUID
    email: str | None
    email_verificado: bool
    company_id: UUID | None


async def destinatarios_de_empresa(session: AsyncSession, company_id: UUID) -> list[Destinatario]:
    """Usuarios activos de una empresa cliente.

    El staff NO tiene membresía de empresa (ADR-0011), así que esta consulta
    devuelve solo clientes. Avisar a Operaciones es otra consulta, no un caso
    especial de esta.
    """
    filas = (
        await session.execute(
            text("""
                SELECT u.id, u.email, u.email_verified_at IS NOT NULL AS verificado,
                       cm.company_id
                FROM company_memberships cm
                JOIN users u ON u.id = cm.user_id
                WHERE cm.company_id = :empresa
                  AND cm.status = 'ACTIVE'
                  AND u.status = 'ACTIVE'
                  AND u.deleted_at IS NULL
            """),
            {"empresa": company_id},
        )
    ).all()

    return [
        Destinatario(
            user_id=f.id,
            email=f.email,
            email_verificado=f.verificado,
            company_id=f.company_id,
        )
        for f in filas
    ]


async def destinatario_por_id(session: AsyncSession, user_id: UUID) -> Destinatario | None:
    """Una persona concreta, para los avisos de su propia cuenta.

    No filtra por estado ni por membresía: un aviso de seguridad o una
    invitación van a esa cuenta y a ninguna otra, sin importar de qué empresa
    sea ni si el alta todavía no terminó.
    """
    fila = (
        await session.execute(
            text("""
                SELECT u.id, u.email, u.email_verified_at IS NOT NULL AS verificado,
                       (SELECT cm.company_id FROM company_memberships cm
                        WHERE cm.user_id = u.id AND cm.status = 'ACTIVE' LIMIT 1) AS empresa
                FROM users u
                WHERE u.id = :u AND u.deleted_at IS NULL
            """),
            {"u": user_id},
        )
    ).one_or_none()

    if fila is None:
        return None

    return Destinatario(
        user_id=fila.id,
        email=fila.email,
        email_verificado=fila.verificado,
        company_id=fila.empresa,
    )


async def destinatarios_de_operaciones(session: AsyncSession) -> list[Destinatario]:
    """Staff interno que debe enterarse de lo que hace un cliente.

    El staff no tiene membresía de empresa (ADR-0011), así que se lo busca por
    rol y no por `company_memberships`. Se excluye a `OPS_AGENT` a propósito:
    el aviso de "hay una solicitud para aprobar" le sirve a quien puede
    aprobarla, y llenarle la bandeja a todo el equipo con avisos que no puede
    accionar termina en que nadie los lee.

    `company_id` queda en `None`: es un aviso interno, no pertenece a la
    empresa que lo originó, y ponerla ahí lo mostraría bajo el filtro de esa
    empresa en la bandeja.
    """
    filas = (
        await session.execute(
            text("""
                SELECT DISTINCT u.id, u.email, u.email_verified_at IS NOT NULL AS verificado
                FROM user_role_assignments ura
                JOIN roles r ON r.id = ura.role_id
                JOIN users u ON u.id = ura.user_id
                WHERE r.code IN ('OPS_ADMIN', 'SUPER_ADMIN')
                  AND (ura.expires_at IS NULL OR ura.expires_at > now())
                  AND u.status = 'ACTIVE'
                  AND u.deleted_at IS NULL
            """)
        )
    ).all()

    return [
        Destinatario(
            user_id=f.id,
            email=f.email,
            email_verificado=f.verificado,
            company_id=None,
        )
        for f in filas
    ]


async def _crear_notificacion(
    session: AsyncSession,
    *,
    destinatario: Destinatario,
    evento: DefinicionEvento,
    resource_type: str | None,
    resource_id: UUID | None,
    dedup_key: str,
    referencia: str | None,
) -> UUID | None:
    """Crea la fila del aviso. Devuelve `None` si ya existía.

    La deduplicación va contra `notification_deliveries`, no contra una columna
    de `notifications`: es la entrega la que no se puede repetir. Se hace con
    un INSERT condicional para que dos workers concurrentes no creen dos avisos
    del mismo hecho para la misma persona.
    """
    fila = (
        await session.execute(
            text("""
                INSERT INTO notifications
                    (user_id, company_id, event_code, is_critical, title, body,
                     resource_type, resource_id)
                SELECT :usuario, :empresa, :codigo, :critico, :titulo, :cuerpo,
                       :recurso_tipo, :recurso_id
                WHERE NOT EXISTS (
                    SELECT 1 FROM notification_deliveries d
                    JOIN notifications n ON n.id = d.notification_id
                    WHERE d.metadata ->> 'dedup_key' = :dedup
                      AND n.user_id = :usuario
                )
                RETURNING id
            """),
            {
                "usuario": destinatario.user_id,
                "empresa": destinatario.company_id,
                "codigo": evento.codigo,
                "critico": evento.critico,
                "titulo": evento.asunto,
                "cuerpo": texto_del_evento(evento, referencia),
                "recurso_tipo": resource_type,
                "recurso_id": resource_id,
                "dedup": dedup_key,
            },
        )
    ).scalar_one_or_none()

    notificacion_id: UUID | None = fila
    return notificacion_id


async def _registrar_entrega(
    session: AsyncSession,
    *,
    notification_id: UUID,
    channel: str,
    status: str,
    target: str | None = None,
    error: str | None = None,
    metadatos: Mapping[str, object] | None = None,
) -> None:
    """Crea o actualiza la entrega de un canal.

    `ON CONFLICT` sobre `(notification_id, channel)`: un reintento actualiza la
    fila existente en vez de agregar otra. Sin esto el registro diría que se
    mandaron tres correos cuando se mandó uno.
    """
    await session.execute(
        text("""
            INSERT INTO notification_deliveries
                (notification_id, channel, status, attempt_count, target, last_error, metadata)
            VALUES (:n, :canal, :estado, 1, :destino, :error, CAST(:metadatos AS JSONB))
            ON CONFLICT (notification_id, channel) DO UPDATE
            SET status = EXCLUDED.status,
                attempt_count = notification_deliveries.attempt_count + 1,
                last_error = EXCLUDED.last_error,
                target = COALESCE(EXCLUDED.target, notification_deliveries.target),
                sent_at = CASE WHEN EXCLUDED.status = 'SENT'
                               THEN now() ELSE notification_deliveries.sent_at END
        """),
        {
            "n": notification_id,
            "canal": channel,
            "estado": status,
            "destino": target,
            "error": error,
            "metadatos": json.dumps(metadatos or {}, default=str, ensure_ascii=False),
        },
    )

    metrics.notificacion_enviada_total.labels(canal=channel, resultado=status).inc()

    if status == DeliveryStatus.SENT:
        await session.execute(
            text("""
                UPDATE notification_deliveries SET sent_at = COALESCE(sent_at, now())
                WHERE notification_id = :n AND channel = :canal
            """),
            {"n": notification_id, "canal": channel},
        )


async def _ya_enviado_por_correo(session: AsyncSession, dedup_key: str, user_id: UUID) -> bool:
    """¿Este usuario ya recibió el correo de este hecho?

    Es la defensa contra el reenvío que ADR-0014 deja abierto: si el worker
    entregó y murió antes de commitear, al reiniciar vuelve a procesar el
    evento. Sin esta comprobación, la persona recibiría el correo dos veces.
    """
    return bool(
        (
            await session.execute(
                text("""
                    SELECT 1 FROM notification_deliveries d
                    JOIN notifications n ON n.id = d.notification_id
                    WHERE d.metadata ->> 'dedup_key' = :dedup
                      AND n.user_id = :usuario
                      AND d.channel = 'EMAIL'
                      AND d.status = 'SENT'
                    LIMIT 1
                """),
                {"dedup": dedup_key, "usuario": user_id},
            )
        ).scalar_one_or_none()
    )


async def notificar(
    session: AsyncSession,
    *,
    event_code: str,
    destinatarios: list[Destinatario],
    resource_type: str | None = None,
    resource_id: UUID | None = None,
    dedup_key: str,
    referencia: str | None = None,
    enlace: str | None = None,
    exigir_correo_verificado: bool = True,
) -> list[UUID]:
    """Avisa a un conjunto de personas por todos los canales que correspondan.

    `referencia` es el identificador que la persona reconoce: el WR, el número
    de factura o el número de solicitud. Es lo único de negocio que ADR-0008
    deja salir en un correo. `enlace` sobreescribe el que se deriva de la ruta
    del catálogo, y hoy solo lo usa la invitación, cuya URL lleva un token.

    `exigir_correo_verificado=False` es para los avisos donde el correo mismo
    ES la verificación: la invitación y la recuperación. Exigirlo ahí sería
    circular — la persona no puede verificar su dirección sin recibir el correo
    que le pide verificarla — y dejaría sin acceso a todas las cuentas
    migradas, que llegaron sin verificar.

    Devuelve los ids de las notificaciones creadas. Un destinatario que ya
    tenía el aviso no genera uno nuevo.
    """
    evento = definicion(event_code)
    if evento is None:
        raise EventoDesconocido(event_code)

    creadas: list[UUID] = []

    for destinatario in destinatarios:
        notification_id = await _crear_notificacion(
            session,
            destinatario=destinatario,
            evento=evento,
            resource_type=resource_type,
            resource_id=resource_id,
            dedup_key=dedup_key,
            referencia=referencia,
        )

        if notification_id is None:
            # Ya existía. Igual se intenta el correo: pudo haberse creado el
            # aviso y haber fallado el envío en una pasada anterior.
            await _reintentar_correo(
                session,
                destinatario=destinatario,
                evento=evento,
                resource_id=resource_id,
                dedup_key=dedup_key,
                referencia=referencia,
                enlace=enlace,
                exigir_correo_verificado=exigir_correo_verificado,
            )
            continue

        creadas.append(notification_id)

        # IN_APP: crear la fila ES la entrega. No hay red de por medio, así que
        # no puede quedar pendiente.
        await _registrar_entrega(
            session,
            notification_id=notification_id,
            channel=Channel.IN_APP,
            status=DeliveryStatus.SENT,
            metadatos={"dedup_key": dedup_key},
        )

        await _entregar_correo(
            session,
            notification_id=notification_id,
            destinatario=destinatario,
            evento=evento,
            resource_id=resource_id,
            dedup_key=dedup_key,
            referencia=referencia,
            enlace=enlace,
            exigir_correo_verificado=exigir_correo_verificado,
        )

    return creadas


async def _entregar_correo(
    session: AsyncSession,
    *,
    notification_id: UUID,
    destinatario: Destinatario,
    evento: DefinicionEvento,
    resource_id: UUID | None,
    dedup_key: str,
    referencia: str | None = None,
    enlace: str | None = None,
    exigir_correo_verificado: bool = True,
) -> None:
    metadatos: dict[str, object] = {
        "dedup_key": dedup_key,
        "plantilla_version": correo.PLANTILLA_VERSION,
    }

    # Sin correo verificado no se envía. Mandar a una dirección no verificada
    # filtraría el aviso a quien haya puesto un correo ajeno al registrarse.
    # La invitación y la recuperación son la excepción: ahí el correo es el
    # mecanismo de verificación, no algo que dependa de ella.
    if not destinatario.email or (exigir_correo_verificado and not destinatario.email_verificado):
        await _registrar_entrega(
            session,
            notification_id=notification_id,
            channel=Channel.EMAIL,
            status=DeliveryStatus.SKIPPED,
            target=destinatario.email,
            error="correo no verificado",
            metadatos=metadatos,
        )
        return

    if await _ya_enviado_por_correo(session, dedup_key, destinatario.user_id):
        return

    compuesto = correo.componer(
        evento,
        resource_id=str(resource_id) if resource_id else None,
        referencia=referencia,
        enlace=enlace,
    )

    try:
        await correo.enviar(destinatario.email, compuesto)
    except correo.EnvioFallido as error:
        await _registrar_entrega(
            session,
            notification_id=notification_id,
            channel=Channel.EMAIL,
            status=DeliveryStatus.FAILED,
            target=destinatario.email,
            error=str(error)[:500],
            metadatos=metadatos,
        )
        # Se relanza: el outbox reintenta el evento entero con backoff.
        raise

    await _registrar_entrega(
        session,
        notification_id=notification_id,
        channel=Channel.EMAIL,
        status=DeliveryStatus.SENT,
        target=destinatario.email,
        metadatos=metadatos,
    )


async def _reintentar_correo(
    session: AsyncSession,
    *,
    destinatario: Destinatario,
    evento: DefinicionEvento,
    resource_id: UUID | None,
    dedup_key: str,
    referencia: str | None = None,
    enlace: str | None = None,
    exigir_correo_verificado: bool = True,
) -> None:
    """El aviso ya existía; solo falta ver si el correo quedó sin enviar."""
    pendiente = (
        await session.execute(
            text("""
                SELECT d.notification_id
                FROM notification_deliveries d
                JOIN notifications n ON n.id = d.notification_id
                WHERE d.metadata ->> 'dedup_key' = :dedup
                  AND n.user_id = :usuario
                  AND d.channel = 'EMAIL'
                  AND d.status = 'FAILED'
                LIMIT 1
            """),
            {"dedup": dedup_key, "usuario": destinatario.user_id},
        )
    ).scalar_one_or_none()

    if pendiente is None:
        return

    await _entregar_correo(
        session,
        notification_id=pendiente,
        destinatario=destinatario,
        evento=evento,
        resource_id=resource_id,
        dedup_key=dedup_key,
        referencia=referencia,
        enlace=enlace,
        exigir_correo_verificado=exigir_correo_verificado,
    )


async def enviar_enlace_de_cuenta(
    session: AsyncSession,
    *,
    user_id: UUID,
    event_code: str,
    token: str,
) -> bool:
    """Manda un correo con un enlace de un solo uso. Devuelve si se entregó.

    **No pasa por el outbox, a diferencia de todo lo demás.** El outbox guarda
    su `payload` en la base, y meter ahí el token en claro anularía el motivo de
    que `one_time_tokens` guarde solo su huella: quien pudiera leer la base
    podría entrar a cualquier cuenta. Se paga con que un relay caído pierde el
    correo, y por eso esto devuelve un booleano en vez de lanzar — el llamador
    decide qué hacer. En el alta hay salida: el administrador todavía tiene la
    contraseña temporal para entregarla a mano.

    La `dedup_key` incluye la huella del token, no el token: dos pedidos de
    recuperación seguidos son dos correos distintos y los dos deben salir, pero
    reprocesar el mismo pedido no debe mandarlo dos veces.
    """
    evento = definicion(event_code)
    if evento is None:
        raise EventoDesconocido(event_code)

    destinatario = await destinatario_por_id(session, user_id)
    if destinatario is None:
        return False

    base = get_settings().frontend_base_url.rstrip("/")
    enlace = f"{base}{evento.ruta.format(id=token)}"

    try:
        await notificar(
            session,
            event_code=event_code,
            destinatarios=[destinatario],
            resource_type="user",
            resource_id=user_id,
            dedup_key=f"cuenta:{event_code}:{fingerprint(token).hex()}",
            enlace=enlace,
            # El correo es el que verifica la dirección; no puede depender de
            # que ya esté verificada.
            exigir_correo_verificado=False,
        )
    except correo.EnvioFallido as error:
        # La entrega ya quedó registrada como FAILED dentro de `notificar`, con
        # su motivo. Acá solo se evita que el fallo tumbe la operación que la
        # originó: crear un usuario o pedir una recuperación.
        _log.warning(
            "no se pudo enviar el enlace de cuenta",
            extra={"evento": event_code, "user_id": str(user_id), "error": str(error)[:200]},
        )
        return False

    return True


# --- Bandeja ---


@dataclass(frozen=True)
class NotificacionResumen:
    id: UUID
    event_code: str
    title: str
    body: str
    is_critical: bool
    resource_type: str | None
    resource_id: UUID | None
    created_at: datetime
    read_at: datetime | None


async def contar_no_leidas(session: AsyncSession, user_id: UUID) -> int:
    total: int = (
        await session.execute(
            text("""
                SELECT count(*) FROM notifications
                WHERE user_id = :u AND read_at IS NULL
            """),
            {"u": user_id},
        )
    ).scalar_one()
    return total


async def marcar_leida(session: AsyncSession, *, notification_id: UUID, user_id: UUID) -> bool:
    """Marca una notificación como leída. `False` si no es de este usuario.

    El filtro por usuario va en el WHERE: traer la fila y descartarla después
    ya habría expuesto el aviso de otra persona al proceso.
    """
    fila = (
        await session.execute(
            text("""
                UPDATE notifications
                SET read_at = COALESCE(read_at, now())
                WHERE id = :id AND user_id = :u
                RETURNING id
            """),
            {"id": notification_id, "u": user_id},
        )
    ).scalar_one_or_none()

    return fila is not None


async def marcar_todas_leidas(session: AsyncSession, user_id: UUID) -> int:
    resultado = await session.execute(
        text("""
            UPDATE notifications SET read_at = now()
            WHERE user_id = :u AND read_at IS NULL
            RETURNING id
        """),
        {"u": user_id},
    )
    return len(resultado.all())


async def listar(
    session: AsyncSession,
    *,
    user_id: UUID,
    limite: int,
    cursor: Cursor | None,
    solo_no_leidas: bool = False,
) -> Pagina[NotificacionResumen]:
    """Bandeja del usuario, paginada por cursor.

    Se pide `limite + 1` fila para saber si hay página siguiente sin contar
    toda la tabla.
    """
    condiciones = ["user_id = :u"]
    parametros: dict[str, object] = {"u": user_id, "limite": limite + 1}

    if solo_no_leidas:
        condiciones.append("read_at IS NULL")

    if cursor is not None:
        # Comparación de tuplas: el orden es (created_at DESC, id DESC), así que
        # la página siguiente es todo lo estrictamente menor que el cursor.
        condiciones.append("(created_at, id) < (:cursor_fecha, :cursor_id)")
        parametros["cursor_fecha"] = cursor.created_at
        parametros["cursor_id"] = cursor.id

    consulta = f"""
        SELECT id, event_code, title, body, is_critical, resource_type,
               resource_id, created_at, read_at
        FROM notifications
        WHERE {" AND ".join(condiciones)}
        ORDER BY created_at DESC, id DESC
        LIMIT :limite
    """  # noqa: S608

    filas = (await session.execute(text(consulta), parametros)).all()

    items = [
        NotificacionResumen(
            id=f.id,
            event_code=f.event_code,
            title=f.title,
            body=f.body,
            is_critical=f.is_critical,
            resource_type=f.resource_type,
            resource_id=f.resource_id,
            created_at=f.created_at,
            read_at=f.read_at,
        )
        for f in filas
    ]

    return armar_pagina(
        items,
        limite=limite,
        cursor_de=lambda n: Cursor(created_at=n.created_at, id=n.id),
    )
