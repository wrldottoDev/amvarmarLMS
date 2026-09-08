"""Bandeja de notificaciones (Paso 4.2).

No hay permiso RBAC de por medio: cada persona ve las suyas y ninguna otra. El
alcance es el propio `user_id` del token, que no viene del cliente y no se puede
falsear desde el payload.
"""

from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_session
from app.core.errors import RecursoNoEncontrado
from app.core.pagination import Cursor, normalizar_limite
from app.modules.auth.dependencies import Actor, actor_actual
from app.modules.notifications import service

router = APIRouter(prefix="/api/v1/notifications", tags=["notifications"])

SesionDb = Annotated[AsyncSession, Depends(get_session)]
ActorDep = Annotated[Actor, Depends(actor_actual)]


class NotificationResponse(BaseModel):
    id: UUID
    event_code: str
    title: str
    body: str
    is_critical: bool
    resource_type: str | None
    resource_id: UUID | None
    created_at: datetime
    read_at: datetime | None


class PaginaNotifications(BaseModel):
    items: list[NotificationResponse]
    next_cursor: str | None
    has_more: bool
    unread_count: int


class MarcadasResponse(BaseModel):
    marcadas: int
    unread_count: int


@router.get("", response_model=PaginaNotifications)
async def listar(
    actor: ActorDep,
    db: SesionDb,
    limit: Annotated[int | None, Query(ge=1, le=100)] = None,
    cursor: str | None = None,
    unread: bool = False,
) -> PaginaNotifications:
    pagina = await service.listar(
        db,
        user_id=actor.user_id,
        limite=normalizar_limite(limit),
        cursor=Cursor.decodificar(cursor) if cursor else None,
        solo_no_leidas=unread,
    )

    return PaginaNotifications(
        items=[NotificationResponse(**vars(n)) for n in pagina.items],
        next_cursor=pagina.next_cursor,
        has_more=pagina.has_more,
        # Va en la misma respuesta para que la interfaz no tenga que pedir el
        # contador aparte en cada refresco de la bandeja.
        unread_count=await service.contar_no_leidas(db, actor.user_id),
    )


@router.post("/{notification_id}/read", response_model=MarcadasResponse)
async def marcar_leida(
    notification_id: UUID,
    actor: ActorDep,
    db: SesionDb,
) -> MarcadasResponse:
    encontrada = await service.marcar_leida(
        db, notification_id=notification_id, user_id=actor.user_id
    )

    if not encontrada:
        # 404 y no 403: confirmar que la notificación existe ya diría algo de
        # otra persona.
        raise RecursoNoEncontrado("Notificación no encontrada.")

    await db.commit()
    return MarcadasResponse(
        marcadas=1, unread_count=await service.contar_no_leidas(db, actor.user_id)
    )


@router.post("/read-all", status_code=status.HTTP_200_OK, response_model=MarcadasResponse)
async def marcar_todas(actor: ActorDep, db: SesionDb) -> MarcadasResponse:
    marcadas = await service.marcar_todas_leidas(db, actor.user_id)
    await db.commit()
    return MarcadasResponse(marcadas=marcadas, unread_count=0)
