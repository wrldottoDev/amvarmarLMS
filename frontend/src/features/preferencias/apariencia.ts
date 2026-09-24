"use client";

import { useCallback, useEffect, useSyncExternalStore } from "react";

/**
 * Tema y sidebar, con las mismas claves de `localStorage` que el sistema viejo.
 *
 * Se conservan a propósito (`amv:sidebar:expanded`, `amv:theme`): quien ya
 * trabajaba con el sidebar plegado se lo encuentra plegado la primera vez que
 * entra al sistema nuevo, en el mismo navegador.
 *
 * Se lee con `useSyncExternalStore` y no con un efecto que llame a `setState`:
 * `localStorage` ES un almacén externo, el servidor no lo tiene, y esta es la
 * API que React ofrece para leerlo sin romper la hidratación ni provocar un
 * segundo render en cada montaje.
 */
const CLAVE_SIDEBAR = "amv:sidebar:expanded";
const CLAVE_TEMA = "amv:theme";

export type Tema = "claro" | "oscuro";

// Suscriptores propios: el evento `storage` del navegador solo avisa a las
// OTRAS pestañas, así que sin esto un cambio no se vería en la pestaña que lo
// hizo. Escuchar los dos deja las pestañas sincronizadas entre sí.
const oyentes = new Set<() => void>();

function avisar(): void {
  for (const oyente of oyentes) oyente();
}

function suscribir(oyente: () => void): () => void {
  oyentes.add(oyente);
  window.addEventListener("storage", oyente);
  return () => {
    oyentes.delete(oyente);
    window.removeEventListener("storage", oyente);
  };
}

function leer(clave: string): string | null {
  try {
    return window.localStorage.getItem(clave);
  } catch {
    // Navegación privada con almacenamiento bloqueado. Se usa el valor por
    // defecto en vez de romper la aplicación entera por una preferencia.
    return null;
  }
}

function guardar(clave: string, valor: string): void {
  try {
    window.localStorage.setItem(clave, valor);
  } catch {
    // Ver `leer`.
  }
  avisar();
}

function usePreferencia(clave: string, activoSi: string): boolean {
  return useSyncExternalStore(
    suscribir,
    () => leer(clave) === activoSi,
    // Instantánea del servidor: sin `localStorage`, el valor por defecto. Tiene
    // que coincidir con el primer render del cliente o React avisa de una
    // discrepancia de hidratación.
    () => false,
  );
}

export function useTema() {
  const oscuro = usePreferencia(CLAVE_TEMA, "oscuro");

  useEffect(() => {
    // La clase va en `body` y no en `html` porque así la tenía el sistema
    // viejo, y los tokens de `globals.css` cuelgan de `body.theme-dark`.
    document.body.classList.toggle("theme-dark", oscuro);
  }, [oscuro]);

  const alternar = useCallback(() => {
    guardar(CLAVE_TEMA, leer(CLAVE_TEMA) === "oscuro" ? "claro" : "oscuro");
  }, []);

  return { tema: (oscuro ? "oscuro" : "claro") as Tema, alternar };
}

export function useSidebar() {
  const expandido = usePreferencia(CLAVE_SIDEBAR, "1");

  const alternar = useCallback(() => {
    guardar(CLAVE_SIDEBAR, leer(CLAVE_SIDEBAR) === "1" ? "0" : "1");
  }, []);

  return { expandido, alternar };
}
