import createClient from "openapi-fetch";
import type { paths } from "./generated";
import { refrescarAccessToken } from "./refresh-mutex";
import { obtenerAccessToken, obtenerVersionCompartida } from "./token-store";

const baseUrl = (process.env.NEXT_PUBLIC_API_BASE_URL ?? "").replace(/\/$/, "");

const rutasSinRefresh = [
  "/api/v1/auth/login",
  "/api/v1/auth/refresh",
  "/api/v1/auth/password/forgot",
  "/api/v1/auth/password/reset",
];

function rutaDe(input: RequestInfo | URL) {
  if (input instanceof Request) return new URL(input.url).pathname;
  return new URL(input.toString(), window.location.origin).pathname;
}

function conAutorizacion(input: RequestInfo | URL, init: RequestInit | undefined, token: string | null) {
  const headers = new Headers(input instanceof Request ? input.headers : undefined);
  new Headers(init?.headers).forEach((valor, nombre) => headers.set(nombre, valor));
  if (token) headers.set("Authorization", `Bearer ${token}`);
  headers.set("Accept", "application/json");
  return { ...init, headers, credentials: "include" as const };
}

const fetchAutenticado: typeof fetch = async (input, init) => {
  const ruta = rutaDe(input);
  const tokenInicial = obtenerAccessToken();
  const versionInicial = obtenerVersionCompartida();
  const copiaParaReintento = input instanceof Request ? input.clone() : input;
  let respuesta = await fetch(input, conAutorizacion(input, init, tokenInicial));

  if (respuesta.status !== 401 || rutasSinRefresh.includes(ruta)) {
    return respuesta;
  }

  const tokenRenovado = await refrescarAccessToken(tokenInicial, versionInicial);
  if (!tokenRenovado) return respuesta;

  respuesta = await fetch(
    copiaParaReintento,
    conAutorizacion(copiaParaReintento, init, tokenRenovado),
  );
  return respuesta;
};

export const api = createClient<paths>({
  baseUrl,
  fetch: fetchAutenticado,
});

type DetalleError = Record<string, unknown> | unknown[] | string | number | boolean | null;

export class ErrorApi extends Error {
  readonly status: number;
  readonly code: string;
  readonly details: DetalleError | undefined;
  readonly requestId: string | undefined;
  readonly retryAfter: string | null;

  constructor(
    message: string,
    opciones: {
      status: number;
      code?: string;
      details?: DetalleError;
      requestId?: string;
      retryAfter?: string | null;
    },
  ) {
    super(message);
    this.name = "ErrorApi";
    this.status = opciones.status;
    this.code = opciones.code ?? "ERROR_INESPERADO";
    this.details = opciones.details;
    this.requestId = opciones.requestId;
    this.retryAfter = opciones.retryAfter ?? null;
  }
}

function esRegistro(valor: unknown): valor is Record<string, unknown> {
  return Boolean(valor) && typeof valor === "object" && !Array.isArray(valor);
}

export function convertirErrorApi(error: unknown, respuesta: Response) {
  if (esRegistro(error) && esRegistro(error.error)) {
    const contenido = error.error;
    return new ErrorApi(
      typeof contenido.message === "string" ? contenido.message : "No fue posible completar la solicitud.",
      {
        status: respuesta.status,
        code: typeof contenido.code === "string" ? contenido.code : undefined,
        details: contenido.details as DetalleError | undefined,
        requestId: typeof contenido.request_id === "string" ? contenido.request_id : undefined,
        retryAfter: respuesta.headers.get("Retry-After"),
      },
    );
  }

  return new ErrorApi("No fue posible completar la solicitud.", {
    status: respuesta.status,
    retryAfter: respuesta.headers.get("Retry-After"),
  });
}

export function exigirDatos<T>(resultado: {
  data?: T;
  error?: unknown;
  response: Response;
}): T {
  if (resultado.data !== undefined) return resultado.data;
  throw convertirErrorApi(resultado.error, resultado.response);
}
