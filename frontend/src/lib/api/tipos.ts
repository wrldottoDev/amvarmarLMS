import type { components } from "./generated";

export type UsuarioActual = components["schemas"]["MeResponse"];
export type Empresa = components["schemas"]["EmpresaResponse"];
export type TokenResponse = components["schemas"]["TokenResponse"];
export type SesionActiva = components["schemas"]["SessionResponse"];
export type CargaResumen = components["schemas"]["ShipmentResumenResponse"];
export type CargaDetalle = components["schemas"]["ShipmentDetalleResponse"];
export type PaginaCargas = components["schemas"]["PaginaShipments"];
export type PaginaTimeline = components["schemas"]["PaginaTimeline"];
export type EventoCarga = components["schemas"]["EventoResponse"];
export type Dashboard = components["schemas"]["DashboardResponse"];
export type EstadoCarga = components["schemas"]["ShipmentStatus"];
export type SolicitudTransicion = components["schemas"]["TransitionRequest"];
