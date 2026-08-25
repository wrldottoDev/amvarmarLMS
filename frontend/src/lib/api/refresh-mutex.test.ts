import { afterEach, describe, expect, it, vi } from "vitest";

const respuestaValida = {
  access_token: "access-token-nuevo",
  token_type: "bearer",
  expires_at: "2026-08-24T20:00:00Z",
};

afterEach(() => {
  vi.unstubAllGlobals();
  vi.resetModules();
});

describe("mutex de refresh", () => {
  it("comparte una sola rotación entre solicitudes concurrentes", async () => {
    let liberar: (() => void) | undefined;
    const espera = new Promise<void>((resolver) => {
      liberar = resolver;
    });
    const fetchMock = vi.fn(async () => {
      await espera;
      return new Response(JSON.stringify(respuestaValida), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    });
    vi.stubGlobal("fetch", fetchMock);

    const { refrescarAccessToken } = await import("./refresh-mutex");
    const primera = refrescarAccessToken();
    const segunda = refrescarAccessToken();

    await vi.waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    liberar?.();

    await expect(Promise.all([primera, segunda])).resolves.toEqual([
      respuestaValida.access_token,
      respuestaValida.access_token,
    ]);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("permite un nuevo intento después de terminar el anterior", async () => {
    const fetchMock = vi.fn(async () =>
      new Response(JSON.stringify(respuestaValida), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const { refrescarAccessToken } = await import("./refresh-mutex");
    await refrescarAccessToken();
    await refrescarAccessToken();

    expect(fetchMock).toHaveBeenCalledTimes(2);
  });
});
