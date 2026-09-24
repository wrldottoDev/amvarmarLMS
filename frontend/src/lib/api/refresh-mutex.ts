import type { TokenResponse } from "./tipos";
import {
  guardarAccessToken,
  esperarTokenCompartido,
  limpiarAccessToken,
  obtenerAccessToken,
  obtenerVersionCompartida,
  obtenerVersionToken,
} from "./token-store";

const baseUrl = (process.env.NEXT_PUBLIC_API_BASE_URL ?? "").replace(/\/$/, "");
const NOMBRE_LOCK = "amvarmar-refresh-token";

let refreshEnCurso: Promise<string | null> | null = null;

function esTokenResponse(valor: unknown): valor is TokenResponse {
  if (!valor || typeof valor !== "object") return false;
  const candidato = valor as Record<string, unknown>;
  return typeof candidato.access_token === "string" && typeof candidato.expires_at === "string";
}

async function solicitarRefresh() {
  const respuesta = await fetch(`${baseUrl}/api/v1/auth/refresh`, {
    method: "POST",
    credentials: "include",
    headers: { Accept: "application/json" },
  });

  if (!respuesta.ok) {
    limpiarAccessToken();
    return null;
  }

  const cuerpo: unknown = await respuesta.json();
  if (!esTokenResponse(cuerpo)) {
    limpiarAccessToken();
    return null;
  }

  guardarAccessToken(cuerpo.access_token);
  return cuerpo.access_token;
}

async function usarTokenRotadoPorOtraPestana(
  tokenRechazado: string | null,
  versionRechazada: string,
) {
  const tokenActual = obtenerAccessToken();
  if (tokenRechazado && tokenActual && tokenActual !== tokenRechazado) {
    return tokenActual;
  }

  const versionCompartida = obtenerVersionCompartida();
  if (versionCompartida !== versionRechazada) {
    return esperarTokenCompartido(versionCompartida);
  }

  return null;
}

async function ejecutarConLockEntrePestanas(
  tokenRechazado: string | null,
  versionRechazada: string,
) {
  const tokenYaRotado = await usarTokenRotadoPorOtraPestana(tokenRechazado, versionRechazada);
  if (tokenYaRotado) return tokenYaRotado;

  if (typeof window === "undefined" || typeof navigator === "undefined" || !("locks" in navigator)) {
    return solicitarRefresh();
  }

  const versionAntes = obtenerVersionToken();

  return navigator.locks.request(NOMBRE_LOCK, async () => {
    const tokenRotado = await usarTokenRotadoPorOtraPestana(tokenRechazado, versionRechazada);
    if (tokenRotado) return tokenRotado;

    const tokenCompartido = obtenerAccessToken();
    const tokenFueReemplazado = tokenRechazado
      ? tokenCompartido !== tokenRechazado
      : obtenerVersionToken() !== versionAntes;
    if (tokenCompartido && tokenFueReemplazado) {
      return tokenCompartido;
    }

    return solicitarRefresh();
  });
}

export function refrescarAccessToken(
  tokenRechazado: string | null = null,
  versionRechazada = obtenerVersionCompartida(),
) {
  if (refreshEnCurso) return refreshEnCurso;

  refreshEnCurso = ejecutarConLockEntrePestanas(tokenRechazado, versionRechazada).finally(() => {
    refreshEnCurso = null;
  });

  return refreshEnCurso;
}
