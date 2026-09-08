type SuscriptorToken = (token: string | null) => void;

type MensajeSesion =
  | { tipo: "token"; token: string; version: string }
  | { tipo: "logout"; version: string };

let accessToken: string | null = null;
let versionToken = "inicio";
let canal: BroadcastChannel | null = null;
const suscriptores = new Set<SuscriptorToken>();
const CLAVE_VERSION = "amvarmar:token-version";

function crearVersion() {
  return `${Date.now()}-${crypto.randomUUID()}`;
}

function notificar() {
  suscriptores.forEach((suscriptor) => suscriptor(accessToken));
}

function guardarVersionCompartida(version: string) {
  if (typeof window !== "undefined") {
    window.localStorage.setItem(CLAVE_VERSION, version);
  }
}

function obtenerCanal() {
  if (typeof window === "undefined" || typeof BroadcastChannel === "undefined") {
    return null;
  }

  if (!canal) {
    canal = new BroadcastChannel("amvarmar-sesion");
    canal.addEventListener("message", (evento: MessageEvent<MensajeSesion>) => {
      const mensaje = evento.data;
      if (mensaje.tipo === "token") {
        accessToken = mensaje.token;
        versionToken = mensaje.version;
      } else if (mensaje.tipo === "logout") {
        accessToken = null;
        versionToken = mensaje.version;
      }
      notificar();
    });
  }

  return canal;
}

export function obtenerAccessToken() {
  obtenerCanal();
  return accessToken;
}

export function obtenerVersionToken() {
  obtenerCanal();
  return versionToken;
}

export function obtenerVersionCompartida() {
  if (typeof window === "undefined") return versionToken;
  return window.localStorage.getItem(CLAVE_VERSION) ?? versionToken;
}

export function guardarAccessToken(token: string, compartir = true) {
  accessToken = token;
  versionToken = crearVersion();
  guardarVersionCompartida(versionToken);
  notificar();

  if (compartir) {
    obtenerCanal()?.postMessage({ tipo: "token", token, version: versionToken } satisfies MensajeSesion);
  }
}

export function limpiarAccessToken(compartir = true) {
  accessToken = null;
  versionToken = crearVersion();
  guardarVersionCompartida(versionToken);
  notificar();

  if (compartir) {
    obtenerCanal()?.postMessage({ tipo: "logout", version: versionToken } satisfies MensajeSesion);
  }
}

export function suscribirToken(suscriptor: SuscriptorToken) {
  obtenerCanal();
  suscriptores.add(suscriptor);
  return () => suscriptores.delete(suscriptor);
}

export function esperarTokenCompartido(version: string, esperaMs = 500) {
  if (accessToken && versionToken === version) return Promise.resolve(accessToken);

  return new Promise<string | null>((resolver) => {
    const temporizador = globalThis.setTimeout(() => {
      cancelar();
      resolver(null);
    }, esperaMs);
    const cancelar = suscribirToken((token) => {
      if (token && versionToken === version) {
        globalThis.clearTimeout(temporizador);
        cancelar();
        resolver(token);
      }
    });
  });
}
