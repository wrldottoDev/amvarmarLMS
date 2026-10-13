"""Calculadora de impuestos de prueba, solo para AMVARMAR (estimador, fase F1).

Las tasas se ponen a mano: todavía no hay catálogo arancelario. No es la
calculadora pública del diseño (docs/estimador-impuestos/02), que necesita
catálogo verificado, clasificador y endurecimiento de seguridad.
"""

from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_session
from app.core.errors import SinPermiso
from app.core.redis import get_redis
from app.modules.aranceles.motor import Entrada, Politica, Tasas, calcular
from app.modules.auth.dependencies import Actor, actor_actual
from app.modules.rbac.catalog import Perm
from app.modules.rbac.service import obtener_permisos_efectivos

router = APIRouter(prefix="/api/v1/aranceles", tags=["aranceles"])

SesionDb = Annotated[AsyncSession, Depends(get_session)]
RedisDep = Annotated[Redis, Depends(get_redis)]
ActorDep = Annotated[Actor, Depends(actor_actual)]

# Topes de cordura: un error de tipeo no puede pedir un cálculo absurdo.
Monto = Annotated[Decimal, Field(ge=0, le=Decimal("1000000000"), max_digits=16, decimal_places=4)]
Tasa = Annotated[Decimal, Field(ge=0, le=Decimal("1000"), max_digits=9, decimal_places=4)]


class EstimarRequest(BaseModel):
    valor_mercancia_usd: Annotated[Monto, Field(gt=0)]
    flete_usd: Monto = Decimal("0")
    seguro_usd: Monto = Decimal("0")
    tipo_cambio: Annotated[
        Decimal, Field(gt=0, le=Decimal("100000"), max_digits=12, decimal_places=4)
    ]
    dai: Tasa
    ley_6946: Tasa = Decimal("1")
    selectivo: Tasa = Decimal("0")
    margen_ge: Tasa = Decimal("0")
    iva: Tasa = Decimal("13")
    otros_cargos_crc: Monto = Decimal("0")
    sc_incluye_ley_6946: bool = False


class LineaResponse(BaseModel):
    concepto: str
    base: Decimal
    tasa: Decimal | None
    monto: Decimal
    suma_al_total: bool
    fuente: str


class EstimarResponse(BaseModel):
    valor_aduanero_usd: Decimal
    valor_aduanero_crc: Decimal
    lineas: list[LineaResponse]
    total: Decimal
    reglas_version: str
    sc_incluye_ley_6946: bool
    redondeo: str


@router.post("/estimar", response_model=EstimarResponse)
async def estimar(
    datos: EstimarRequest, actor: ActorDep, db: SesionDb, redis: RedisDep
) -> EstimarResponse:
    permisos = await obtener_permisos_efectivos(db, redis, actor.user_id)
    if not permisos.permite(Perm.SYSTEM_SETTINGS_MANAGE):
        raise SinPermiso("La calculadora de prueba es solo para AMVARMAR.")

    desglose = calcular(
        Entrada(
            valor_mercancia_usd=datos.valor_mercancia_usd,
            flete_usd=datos.flete_usd,
            seguro_usd=datos.seguro_usd,
            tipo_cambio=datos.tipo_cambio,
            tasas=Tasas(
                dai=datos.dai,
                ley_6946=datos.ley_6946,
                selectivo=datos.selectivo,
                margen_ge=datos.margen_ge,
                iva=datos.iva,
            ),
            otros_cargos_crc=datos.otros_cargos_crc,
            politica=Politica(sc_incluye_ley_6946=datos.sc_incluye_ley_6946),
        )
    )
    return EstimarResponse(
        valor_aduanero_usd=desglose.valor_aduanero_usd,
        valor_aduanero_crc=desglose.valor_aduanero_crc,
        lineas=[LineaResponse(**vars(linea)) for linea in desglose.lineas],
        total=desglose.total,
        reglas_version=desglose.reglas_version,
        sc_incluye_ley_6946=desglose.politica.sc_incluye_ley_6946,
        redondeo=desglose.politica.redondeo,
    )
