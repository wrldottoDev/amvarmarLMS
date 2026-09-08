"""Dashboard del cliente y de operaciones (sección 6 del documento de arquitectura)."""

from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_session
from app.core.redis import get_redis
from app.modules.auth.dependencies import Actor, actor_actual
from app.modules.rbac.service import obtener_permisos_efectivos
from app.modules.shipments import queries
from app.modules.shipments.router import ShipmentResumenResponse, _a_resumen

router = APIRouter(prefix="/api/v1/dashboard", tags=["dashboard"])

SesionDb = Annotated[AsyncSession, Depends(get_session)]
RedisDep = Annotated[Redis, Depends(get_redis)]
ActorDep = Annotated[Actor, Depends(actor_actual)]


class TarjetasResponse(BaseModel):
    en_bodega: int
    en_transito: int
    proximos_a_llegar: int
    requieren_accion: int
    entregados_este_mes: int


class DashboardResponse(BaseModel):
    tarjetas: TarjetasResponse
    proximos_movimientos: list[ShipmentResumenResponse]


async def _armar(
    db: AsyncSession, redis: Redis, actor: Actor, *, solo_del_cliente: bool
) -> DashboardResponse:
    permisos = await obtener_permisos_efectivos(db, redis, actor.user_id)

    tarjetas = await queries.tarjetas_dashboard(
        db, permisos=permisos, solo_del_cliente=solo_del_cliente
    )
    movimientos = await queries.proximos_movimientos(db, permisos=permisos)

    return DashboardResponse(
        tarjetas=TarjetasResponse(
            en_bodega=tarjetas.en_bodega,
            en_transito=tarjetas.en_transito,
            proximos_a_llegar=tarjetas.proximos_a_llegar,
            requieren_accion=tarjetas.requieren_accion,
            entregados_este_mes=tarjetas.entregados_este_mes,
        ),
        proximos_movimientos=[_a_resumen(f) for f in movimientos],
    )


@router.get("/client", response_model=DashboardResponse)
async def dashboard_cliente(actor: ActorDep, db: SesionDb, redis: RedisDep) -> DashboardResponse:
    """Vista del cliente.

    "Requieren acción" cuenta solo lo que le toca al cliente: un packing list
    pendiente es de Operaciones y no debe aparecer aquí (ADR-0003).
    """
    return await _armar(db, redis, actor, solo_del_cliente=True)


@router.get("/operations", response_model=DashboardResponse)
async def dashboard_operaciones(
    actor: ActorDep, db: SesionDb, redis: RedisDep
) -> DashboardResponse:
    """Vista de Operaciones: cuenta todo lo pendiente, sea de quien sea."""
    return await _armar(db, redis, actor, solo_del_cliente=False)
