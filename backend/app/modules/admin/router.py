"""Administración de empresas y usuarios.

Reemplaza lo que en el sistema viejo se hacía desde el admin de Django. La
diferencia: acá cada acción pasa por el mismo control de permisos y alcance que
el resto de la API, y queda auditada.
"""

from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel, EmailStr, Field
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from app.core.database import get_session
from app.core.redis import get_redis
from app.modules.admin import service
from app.modules.audit.service import registrar
from app.modules.auth.dependencies import Actor, actor_actual
from app.modules.rbac.service import obtener_permisos_efectivos

router = APIRouter(prefix="/api/v1/admin", tags=["administración"])

SesionDb = Annotated[AsyncSession, Depends(get_session)]
RedisDep = Annotated[Redis, Depends(get_redis)]
ActorDep = Annotated[Actor, Depends(actor_actual)]


def _ip(request: Request) -> str | None:
    return request.client.host if request.client else None


# --- Empresas ---


class EmpresaAdminResponse(BaseModel):
    id: UUID
    legal_name: str
    trade_name: str | None
    tax_id: str | None
    status: str
    usuarios: int
    cargas: int
    created_at: datetime


class CrearEmpresaRequest(BaseModel):
    legal_name: str = Field(min_length=1, max_length=255)
    trade_name: str | None = Field(default=None, max_length=255)
    tax_id: str | None = Field(default=None, max_length=40)


class ActualizarEmpresaRequest(BaseModel):
    legal_name: str | None = Field(default=None, min_length=1, max_length=255)
    trade_name: str | None = Field(default=None, max_length=255)
    tax_id: str | None = Field(default=None, max_length=40)
    status: str | None = Field(default=None, pattern="^(ACTIVE|SUSPENDED|CLOSED)$")


class CreadoResponse(BaseModel):
    id: UUID


@router.get("/companies", response_model=list[EmpresaAdminResponse])
async def listar_empresas(
    actor: ActorDep, db: SesionDb, redis: RedisDep, incluir_inactivas: bool = False
) -> list[EmpresaAdminResponse]:
    permisos = await obtener_permisos_efectivos(db, redis, actor.user_id)
    empresas = await service.listar_empresas(
        db, permisos=permisos, incluir_inactivas=incluir_inactivas
    )
    return [EmpresaAdminResponse(**vars(e)) for e in empresas]


@router.post("/companies", response_model=CreadoResponse, status_code=status.HTTP_201_CREATED)
async def crear_empresa(
    datos: CrearEmpresaRequest, request: Request, actor: ActorDep, db: SesionDb, redis: RedisDep
) -> CreadoResponse:
    permisos = await obtener_permisos_efectivos(db, redis, actor.user_id)
    empresa_id = await service.crear_empresa(
        db,
        legal_name=datos.legal_name,
        trade_name=datos.trade_name,
        tax_id=datos.tax_id,
        actor_user_id=actor.user_id,
        permisos=permisos,
    )

    await registrar(
        db,
        action="company.created",
        resource_type="company",
        resource_id=empresa_id,
        actor_user_id=actor.user_id,
        company_id=empresa_id,
        after_data={"legal_name": datos.legal_name},
        ip_address=_ip(request),
    )
    await db.commit()
    return CreadoResponse(id=empresa_id)


@router.patch("/companies/{company_id}", status_code=status.HTTP_204_NO_CONTENT)
async def actualizar_empresa(
    company_id: UUID,
    datos: ActualizarEmpresaRequest,
    request: Request,
    actor: ActorDep,
    db: SesionDb,
    redis: RedisDep,
) -> None:
    permisos = await obtener_permisos_efectivos(db, redis, actor.user_id)
    cambios = datos.model_dump(exclude_unset=True)
    await service.actualizar_empresa(db, company_id=company_id, cambios=cambios, permisos=permisos)

    await registrar(
        db,
        action="company.updated",
        resource_type="company",
        resource_id=company_id,
        actor_user_id=actor.user_id,
        company_id=company_id,
        after_data={"campos": sorted(cambios)},
        ip_address=_ip(request),
    )
    await db.commit()


class DesactivacionResponse(BaseModel):
    usuarios_suspendidos: int


@router.delete("/companies/{company_id}", response_model=DesactivacionResponse)
async def desactivar_empresa(
    company_id: UUID, request: Request, actor: ActorDep, db: SesionDb, redis: RedisDep
) -> DesactivacionResponse:
    """Desactiva la empresa y suspende a sus usuarios. No borra nada.

    Sus cargas, documentos y auditoría siguen existiendo: borrarla dejaría todo
    ese historial apuntando a una empresa que ya no está.
    """
    permisos = await obtener_permisos_efectivos(db, redis, actor.user_id)
    suspendidos = await service.desactivar_empresa(db, company_id=company_id, permisos=permisos)

    await registrar(
        db,
        action="company.deactivated",
        resource_type="company",
        resource_id=company_id,
        actor_user_id=actor.user_id,
        company_id=company_id,
        after_data={"usuarios_suspendidos": suspendidos},
        ip_address=_ip(request),
    )
    await db.commit()
    return DesactivacionResponse(usuarios_suspendidos=suspendidos)


# --- Usuarios ---


class UsuarioAdminResponse(BaseModel):
    id: UUID
    email: str
    first_name: str
    last_name: str
    phone: str | None
    status: str
    role_code: str | None
    company_id: UUID | None
    company_name: str | None
    last_login_at: datetime | None
    created_at: datetime


class CrearUsuarioRequest(BaseModel):
    email: EmailStr
    first_name: str = Field(min_length=1, max_length=80)
    last_name: str = Field(min_length=1, max_length=80)
    role_code: str
    # Obligatorio para roles de cliente; prohibido para internos (ADR-0011).
    company_id: UUID | None = None
    phone: str | None = Field(default=None, max_length=40)


class UsuarioCreadoResponse(BaseModel):
    id: UUID
    email: str
    # Se muestra UNA vez. No se guarda en claro y no se puede volver a consultar.
    password_temporal: str
    # `False` si el correo de invitación no salió. La pantalla lo usa para
    # decidir si insiste con la contraseña temporal o solo la ofrece como
    # respaldo.
    invitacion_enviada: bool


class ActualizarUsuarioRequest(BaseModel):
    first_name: str | None = Field(default=None, min_length=1, max_length=80)
    last_name: str | None = Field(default=None, min_length=1, max_length=80)
    phone: str | None = Field(default=None, max_length=40)
    status: str | None = Field(default=None, pattern="^(ACTIVE|SUSPENDED|DISABLED)$")
    role_code: str | None = None


@router.get("/users", response_model=list[UsuarioAdminResponse])
async def listar_usuarios(
    actor: ActorDep,
    db: SesionDb,
    redis: RedisDep,
    company_id: UUID | None = None,
    incluir_inactivos: bool = False,
) -> list[UsuarioAdminResponse]:
    permisos = await obtener_permisos_efectivos(db, redis, actor.user_id)
    usuarios = await service.listar_usuarios(
        db, permisos=permisos, company_id=company_id, incluir_inactivos=incluir_inactivos
    )
    return [UsuarioAdminResponse(**vars(u)) for u in usuarios]


@router.post("/users", response_model=UsuarioCreadoResponse, status_code=status.HTTP_201_CREATED)
async def crear_usuario(
    datos: CrearUsuarioRequest, request: Request, actor: ActorDep, db: SesionDb, redis: RedisDep
) -> UsuarioCreadoResponse:
    permisos = await obtener_permisos_efectivos(db, redis, actor.user_id)
    creado = await service.crear_usuario(
        db,
        email=str(datos.email),
        first_name=datos.first_name,
        last_name=datos.last_name,
        role_code=datos.role_code,
        company_id=datos.company_id,
        phone=datos.phone,
        permisos=permisos,
    )

    await registrar(
        db,
        action="user.created",
        resource_type="user",
        resource_id=creado.id,
        actor_user_id=actor.user_id,
        company_id=datos.company_id,
        # La contraseña temporal NO va a la auditoría: el registro de auditoría
        # se conserva para siempre y se consulta desde la interfaz.
        after_data={"email": creado.email, "role_code": datos.role_code},
        ip_address=_ip(request),
    )
    await db.commit()

    return UsuarioCreadoResponse(**vars(creado))


@router.patch("/users/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def actualizar_usuario(
    user_id: UUID,
    datos: ActualizarUsuarioRequest,
    request: Request,
    actor: ActorDep,
    db: SesionDb,
    redis: RedisDep,
) -> None:
    permisos = await obtener_permisos_efectivos(db, redis, actor.user_id)
    cambios = datos.model_dump(exclude_unset=True)
    await service.actualizar_usuario(db, user_id=user_id, cambios=cambios, permisos=permisos)

    await registrar(
        db,
        action="user.updated",
        resource_type="user",
        resource_id=user_id,
        actor_user_id=actor.user_id,
        after_data={"campos": sorted(cambios)},
        ip_address=_ip(request),
    )
    await db.commit()


class ContrasenaTemporalResponse(BaseModel):
    password_temporal: str


@router.post("/users/{user_id}/reset-password", response_model=ContrasenaTemporalResponse)
async def restablecer_contrasena(
    user_id: UUID, request: Request, actor: ActorDep, db: SesionDb, redis: RedisDep
) -> ContrasenaTemporalResponse:
    """Genera una contraseña temporal y corta todas las sesiones del usuario."""
    permisos = await obtener_permisos_efectivos(db, redis, actor.user_id)
    temporal = await service.restablecer_contrasena(db, user_id=user_id, permisos=permisos)

    await registrar(
        db,
        action="user.password_reset",
        resource_type="user",
        resource_id=user_id,
        actor_user_id=actor.user_id,
        ip_address=_ip(request),
    )
    await db.commit()

    return ContrasenaTemporalResponse(password_temporal=temporal)


@router.delete("/users/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def desactivar_usuario(
    user_id: UUID, request: Request, actor: ActorDep, db: SesionDb, redis: RedisDep
) -> None:
    """Desactiva la cuenta y revoca sus sesiones. No borra el usuario.

    Sus cargas creadas y su rastro de auditoría siguen apuntando a él.
    """
    permisos = await obtener_permisos_efectivos(db, redis, actor.user_id)
    await service.desactivar_usuario(db, user_id=user_id, permisos=permisos)

    await registrar(
        db,
        action="user.deactivated",
        resource_type="user",
        resource_id=user_id,
        actor_user_id=actor.user_id,
        ip_address=_ip(request),
    )
    await db.commit()
