/**
 * Roles dichos por lo que la persona puede hacer, no por su código.
 *
 * `CLIENTE` no le dice nada a quien está dando de alta a un empleado. La
 * descripción sí: es lo que necesita para elegir bien a la primera.
 *
 * Son tres (ADR-0017). Antes eran cinco, pero dos pares hacían exactamente lo
 * mismo, así que elegir entre ellos era una decisión sin consecuencia.
 */
export const rolesDeCliente = [
  {
    codigo: "CLIENTE",
    etiqueta: "Cliente",
    descripcion: "Ve el inventario de su empresa, pide despachos y sube los documentos.",
  },
] as const;

export const rolesInternos = [
  {
    codigo: "ADMIN",
    etiqueta: "Administrador",
    descripcion: "Registra cargas, mueve estados, aprueba despachos y gestiona documentos.",
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
