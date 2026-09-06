"""Catálogo de roles y permisos — fuente única de verdad (ADR-0004).

El seed (`scripts/seed_rbac.py`) y los tests de la matriz leen de aquí. Cambiar
un permiso se hace en este archivo y en ADR-0004, en ningún otro lado.
"""

from dataclasses import dataclass

from app.modules.rbac.models import RoleCode, ScopeType


@dataclass(frozen=True)
class RoleDefinition:
    name: str
    description: str
    allowed_scopes: tuple[ScopeType, ...]
    permissions: frozenset[str]


class Perm:
    """Códigos de permiso. Constantes, no strings sueltos en el código.

    Usar `Perm.SHIPMENTS_READ` en vez de `"shipments.read"` hace que un typo
    falle al importar, no en tiempo de ejecución dentro de un endpoint.
    """

    SHIPMENTS_READ = "shipments.read"
    SHIPMENTS_CREATE = "shipments.create"
    SHIPMENTS_UPDATE = "shipments.update"
    SHIPMENTS_TRANSITION_FORWARD = "shipments.transition.forward"
    SHIPMENTS_TRANSITION_BACKWARD = "shipments.transition.backward"
    SHIPMENTS_TRANSITION_REVERT_DELIVERED = "shipments.transition.revert_delivered"
    SHIPMENTS_CANCEL_PREALERT = "shipments.cancel.prealert"
    SHIPMENTS_CANCEL_IN_TRANSIT = "shipments.cancel.in_transit"
    SHIPMENTS_REOPEN = "shipments.reopen"
    SHIPMENTS_LEGACY_REVIEW_RESOLVE = "shipments.legacy_review.resolve"
    SHIPMENTS_LEGAL_HOLD_MANAGE = "shipments.legal_hold.manage"
    # ADR-0006: reportar que no se reconoce una entrega ya marcada. No cambia
    # el estado por sí sola — eso lo hace `dispute.resolve` (RESOLVED_CONFIRMED)
    # o `transition.revert_delivered` (RESOLVED_REVERTED, ya exclusivo de
    # SUPER_ADMIN, esta decisión no lo cambia).
    SHIPMENTS_DISPUTE_CREATE = "shipments.dispute.create"
    SHIPMENTS_DISPUTE_RESOLVE = "shipments.dispute.resolve"
    SHIPMENTS_REQUIREMENT_MANAGE = "shipments.requirement.manage"
    # Exonerar es distinto de gestionar: deja avanzar una carga SIN el documento
    # obligatorio. Por eso es permiso propio y no lo tiene OPS_AGENT.
    SHIPMENTS_REQUIREMENT_WAIVE = "shipments.requirement.waive"

    DISPATCH_REQUESTS_CREATE = "dispatch_requests.create"
    DISPATCH_REQUESTS_APPROVE = "dispatch_requests.approve"
    DISPATCH_REQUESTS_REJECT = "dispatch_requests.reject"
    DISPATCH_REQUESTS_PREPARE = "dispatch_requests.prepare"
    DISPATCH_REQUESTS_DISPATCH = "dispatch_requests.dispatch"
    DISPATCH_REQUESTS_COMPLETE = "dispatch_requests.complete"
    # ADR-0013: la restricción por estado (el cliente solo antes de aprobar) no
    # vive en el permiso sino en la política de dominio.
    DISPATCH_REQUESTS_CANCEL = "dispatch_requests.cancel"

    DOCUMENTS_UPLOAD_CLIENT = "documents.upload.client"
    DOCUMENTS_UPLOAD_INTERNAL = "documents.upload.internal"
    DOCUMENTS_VERIFY = "documents.verify"
    DOCUMENTS_INVALIDATE = "documents.invalidate"

    COMPANIES_MANAGE = "companies.manage"
    USERS_CREATE_INTERNAL = "users.create.internal"
    USERS_MANAGE = "users.manage"
    RBAC_MANAGE = "rbac.manage"
    SYSTEM_SETTINGS_MANAGE = "system_settings.manage"
    AUDIT_LOGS_READ = "audit_logs.read"
    REPORTS_EXPORT = "reports.export"

    NOTIFICATIONS_PREFERENCES_OWN = "notifications.preferences.own"
    NOTIFICATIONS_PREFERENCES_COMPANY = "notifications.preferences.company"

    # Asistente virtual (ADR-0012). `copilot.use` habilita conversar; las
    # herramientas concretas exigen además el permiso de la operación que hacen
    # (consultar una carga exige `shipments.read`), así el asistente nunca
    # amplía lo que el usuario ya podía hacer.
    COPILOT_USE = "copilot.use"
    COPILOT_TOOLS_DRAFT = "copilot.tools.draft"


# code -> descripción legible. El orden no importa; el seed inserta por código.
PERMISSIONS: dict[str, str] = {
    Perm.SHIPMENTS_READ: "Ver cargas (el alcance define si son todas o solo las de su empresa)",
    Perm.SHIPMENTS_CREATE: "Crear prealertas",
    Perm.SHIPMENTS_UPDATE: "Editar datos de una prealerta",
    Perm.SHIPMENTS_TRANSITION_FORWARD: "Cambiar estados operativos, incluida la entrega",
    Perm.SHIPMENTS_TRANSITION_BACKWARD: "Corregir estados hacia atrás (exige justificación)",
    Perm.SHIPMENTS_TRANSITION_REVERT_DELIVERED: "Revertir una carga ya entregada",
    Perm.SHIPMENTS_CANCEL_PREALERT: "Cancelar una carga en PRE_ALERT",
    Perm.SHIPMENTS_CANCEL_IN_TRANSIT: "Cancelar una carga en IN_TRANSIT",
    Perm.SHIPMENTS_REOPEN: "Reabrir una carga cancelada",
    Perm.SHIPMENTS_LEGACY_REVIEW_RESOLVE: "Resolver cargas marcadas para revisión legacy",
    Perm.SHIPMENTS_LEGAL_HOLD_MANAGE: "Activar o desactivar retención especial (legal hold)",
    Perm.SHIPMENTS_DISPUTE_CREATE: "Reportar que no se reconoce una entrega",
    Perm.SHIPMENTS_DISPUTE_RESOLVE: "Resolver una inconformidad de entrega",
    Perm.SHIPMENTS_REQUIREMENT_MANAGE: "Abrir, cancelar y resolver requisitos de una carga",
    Perm.SHIPMENTS_REQUIREMENT_WAIVE: "Exonerar un requisito obligatorio (exige justificación)",
    Perm.DISPATCH_REQUESTS_CREATE: "Solicitar despacho",
    Perm.DISPATCH_REQUESTS_APPROVE: "Aprobar una solicitud de despacho",
    Perm.DISPATCH_REQUESTS_REJECT: "Rechazar una solicitud de despacho (motivo obligatorio)",
    Perm.DISPATCH_REQUESTS_PREPARE: "Preparar despacho",
    Perm.DISPATCH_REQUESTS_DISPATCH: "Confirmar salida de bodega",
    Perm.DISPATCH_REQUESTS_COMPLETE: "Cerrar administrativamente un despacho",
    Perm.DISPATCH_REQUESTS_CANCEL: "Cancelar una solicitud de despacho",
    Perm.DOCUMENTS_UPLOAD_CLIENT: "Subir documentos del cliente",
    Perm.DOCUMENTS_UPLOAD_INTERNAL: "Subir documentos internos (packing list, BL)",
    Perm.DOCUMENTS_VERIFY: "Verificar o rechazar documentos",
    Perm.DOCUMENTS_INVALIDATE: "Invalidar documentos (no elimina el archivo)",
    Perm.COMPANIES_MANAGE: "Gestionar empresas cliente",
    Perm.USERS_CREATE_INTERNAL: "Crear usuarios internos de AMVARMAR",
    Perm.USERS_MANAGE: "Gestionar usuarios (el alcance define de qué empresa)",
    Perm.RBAC_MANAGE: "Gestionar roles y permisos",
    Perm.SYSTEM_SETTINGS_MANAGE: "Configurar ajustes de sistema (límites de archivo, etc.)",
    Perm.AUDIT_LOGS_READ: "Consultar la bitácora de auditoría",
    Perm.REPORTS_EXPORT: "Exportar reportes",
    Perm.NOTIFICATIONS_PREFERENCES_OWN: "Configurar las notificaciones propias",
    Perm.NOTIFICATIONS_PREFERENCES_COMPANY: "Configurar las notificaciones de la empresa",
    Perm.COPILOT_USE: "Conversar con el asistente virtual",
    Perm.COPILOT_TOOLS_DRAFT: "Pedirle al asistente que prepare acciones para confirmar",
}


# Los dos roles de cliente comparten EXACTAMENTE la misma matriz.
#
# Antes `CLIENT_USER` era un subconjunto de `CLIENT_ADMIN`, y en la práctica eso
# significaba que una empresa con una sola persona no podía dar de alta a la
# segunda: quien recibía la cuenta inicial quedaba sin `users.manage` y tenía que
# pedirle a Operaciones que le creara los compañeros. AMVARMAR decidió que dentro
# de una empresa cliente todos pueden lo mismo.
#
# Es un solo conjunto y no dos que casualmente coinciden: dos definiciones
# separadas vuelven a divergir en cuanto alguien agrega un permiso a una sola.
# Los códigos de rol se conservan separados porque la distinción puede volver a
# tener contenido, y renombrarlos obligaría a migrar asignaciones existentes.
_CLIENT_PERMS: frozenset[str] = frozenset(
    {
        Perm.SHIPMENTS_READ,
        Perm.SHIPMENTS_CREATE,
        Perm.SHIPMENTS_UPDATE,
        Perm.SHIPMENTS_CANCEL_PREALERT,
        Perm.SHIPMENTS_DISPUTE_CREATE,
        Perm.DISPATCH_REQUESTS_CREATE,
        Perm.DISPATCH_REQUESTS_CANCEL,
        Perm.DOCUMENTS_UPLOAD_CLIENT,
        Perm.USERS_MANAGE,
        Perm.AUDIT_LOGS_READ,
        Perm.REPORTS_EXPORT,
        Perm.NOTIFICATIONS_PREFERENCES_OWN,
        Perm.NOTIFICATIONS_PREFERENCES_COMPANY,
        Perm.COPILOT_USE,
        # ADR-0012, enmienda 2026-09: no amplía capacidades — cada herramienta de
        # propuesta sigue exigiendo el permiso de dominio de la operación que hace
        # (proponer_despacho exige dispatch_requests.create, que el cliente ya
        # tiene). Sin esto, AMVI podía conversar con un cliente pero nunca
        # prepararle una propuesta.
        Perm.COPILOT_TOOLS_DRAFT,
    }
)

# Ninguno de los dos lleva permisos de transición logística: mover una carga por
# la cadena es trabajo de Operaciones, y el alcance `ORGANIZATION` no cambia eso.
_CLIENT_USER_PERMS: frozenset[str] = _CLIENT_PERMS
_CLIENT_ADMIN_PERMS: frozenset[str] = _CLIENT_PERMS

_OPS_AGENT_PERMS: frozenset[str] = frozenset(
    {
        Perm.SHIPMENTS_READ,
        Perm.SHIPMENTS_CREATE,
        Perm.SHIPMENTS_UPDATE,
        Perm.SHIPMENTS_TRANSITION_FORWARD,
        Perm.SHIPMENTS_CANCEL_PREALERT,
        Perm.DISPATCH_REQUESTS_CREATE,
        Perm.DISPATCH_REQUESTS_APPROVE,
        Perm.DISPATCH_REQUESTS_REJECT,
        Perm.DISPATCH_REQUESTS_PREPARE,
        Perm.DISPATCH_REQUESTS_DISPATCH,
        Perm.DISPATCH_REQUESTS_COMPLETE,
        Perm.DISPATCH_REQUESTS_CANCEL,
        Perm.DOCUMENTS_UPLOAD_CLIENT,
        Perm.DOCUMENTS_UPLOAD_INTERNAL,
        Perm.DOCUMENTS_VERIFY,
        Perm.SHIPMENTS_REQUIREMENT_MANAGE,
        Perm.REPORTS_EXPORT,
        Perm.NOTIFICATIONS_PREFERENCES_OWN,
        Perm.COPILOT_USE,
        Perm.COPILOT_TOOLS_DRAFT,
    }
)

# OPS_ADMIN = OPS_AGENT + correcciones, gestión y auditoría.
# No incluye revert_delivered, rbac.manage ni system_settings.manage.
_OPS_ADMIN_PERMS: frozenset[str] = _OPS_AGENT_PERMS | frozenset(
    {
        Perm.SHIPMENTS_TRANSITION_BACKWARD,
        Perm.SHIPMENTS_CANCEL_IN_TRANSIT,
        Perm.SHIPMENTS_REOPEN,
        Perm.SHIPMENTS_LEGACY_REVIEW_RESOLVE,
        Perm.SHIPMENTS_LEGAL_HOLD_MANAGE,
        Perm.SHIPMENTS_DISPUTE_RESOLVE,
        Perm.SHIPMENTS_REQUIREMENT_WAIVE,
        Perm.DOCUMENTS_INVALIDATE,
        Perm.COMPANIES_MANAGE,
        Perm.USERS_CREATE_INTERNAL,
        Perm.USERS_MANAGE,
        Perm.AUDIT_LOGS_READ,
        Perm.NOTIFICATIONS_PREFERENCES_COMPANY,
    }
)

# SUPER_ADMIN tiene todo. Se calcula, no se enumera: un permiso nuevo lo obtiene
# automáticamente y no puede quedarse fuera por olvido.
_SUPER_ADMIN_PERMS: frozenset[str] = frozenset(PERMISSIONS)


ROLES: dict[str, RoleDefinition] = {
    RoleCode.SUPER_ADMIN: RoleDefinition(
        name="Super administrador",
        description=(
            "Administrador técnico y de seguridad. No se usa para operación cotidiana. "
            "Único rol que puede revertir una entrega y gestionar roles/permisos."
        ),
        allowed_scopes=(ScopeType.GLOBAL,),
        permissions=_SUPER_ADMIN_PERMS,
    ),
    RoleCode.OPS_ADMIN: RoleDefinition(
        name="Administrador de operaciones",
        description=(
            "Gestiona cargas, clientes, usuarios internos y documentos. "
            "Corrige estados hacia atrás con justificación."
        ),
        allowed_scopes=(ScopeType.GLOBAL,),
        permissions=_OPS_ADMIN_PERMS,
    ),
    RoleCode.OPS_AGENT: RoleDefinition(
        name="Agente de operaciones",
        description=(
            "Colaborador operativo: recepción, almacenamiento, preparación, despacho y entrega. "
            "Su alcance es GLOBAL o ASSIGNED según el puesto de cada persona."
        ),
        allowed_scopes=(ScopeType.GLOBAL, ScopeType.ASSIGNED),
        permissions=_OPS_AGENT_PERMS,
    ),
    RoleCode.CLIENT_ADMIN: RoleDefinition(
        name="Administrador de empresa cliente",
        description="Gestiona los usuarios y las cargas de su propia empresa.",
        allowed_scopes=(ScopeType.ORGANIZATION,),
        permissions=_CLIENT_ADMIN_PERMS,
    ),
    RoleCode.CLIENT_USER: RoleDefinition(
        name="Usuario de empresa cliente",
        description=(
            "Las mismas capacidades que el administrador de la empresa: cargas, "
            "documentos, despachos y usuarios de su propia empresa."
        ),
        allowed_scopes=(ScopeType.ORGANIZATION,),
        permissions=_CLIENT_USER_PERMS,
    ),
}
