/**
 * Roles dichos por lo que la persona puede hacer, no por su código.
 *
 * `CLIENT_ADMIN` no le dice nada a quien está dando de alta a un empleado. La
 * descripción sí: es lo que necesita para elegir bien a la primera.
 */
// Los dos tienen EXACTAMENTE los mismos permisos (ADR-0004): ve todas las
// cargas, pide despachos, y gestiona a sus compañeros de empresa. Los
// códigos y etiquetas se conservan separados porque la distinción puede
// volver a tener contenido, no porque hoy hagan algo distinto.
export const rolesDeCliente = [
  {
    codigo: "CLIENT_ADMIN",
    etiqueta: "Administrador de la empresa",
    descripcion: "Ve todas las cargas, pide despachos y gestiona a sus compañeros.",
  },
  {
    codigo: "CLIENT_USER",
    etiqueta: "Usuario",
    descripcion: "Ve todas las cargas, pide despachos y gestiona a sus compañeros.",
  },
] as const;

export const rolesInternos = [
  {
    codigo: "OPS_AGENT",
    etiqueta: "Agente de operaciones",
    descripcion: "Día a día: crea cargas, mueve estados, aprueba despachos.",
  },
  {
    codigo: "OPS_ADMIN",
    etiqueta: "Jefe de operaciones",
    descripcion: "Todo lo del agente, más correcciones, exoneraciones y auditoría.",
  },
  {
    codigo: "SUPER_ADMIN",
    etiqueta: "Administrador del sistema",
    descripcion: "Acceso completo, incluida la configuración y los roles.",
  },
] as const;

export const etiquetaRol: Record<string, string> = Object.fromEntries(
  [...rolesDeCliente, ...rolesInternos].map((r) => [r.codigo, r.etiqueta]),
);

export const etiquetaEstadoCuenta: Record<string, string> = {
  ACTIVE: "Activo",
  SUSPENDED: "Suspendido",
  DISABLED: "Deshabilitado",
  INVITED: "Invitado",
  CLOSED: "Cerrada",
};
