import Image from "next/image";

/**
 * Marco de las pantallas de autenticación: una tarjeta de vidrio sobre un
 * fondo propio, oscuro y quieto.
 *
 * El fondo NO sigue el tema del sistema. Es la única parte de la aplicación
 * donde eso tiene sentido: es la portada, se ve antes de saber quién entra, y
 * el efecto de vidrio necesita algo con color debajo para leerse como vidrio y
 * no como un rectángulo gris.
 *
 * Las variables se redefinen para todo el subárbol en vez de tocar `Campo` y
 * `Boton`: esos componentes se usan en cien pantallas más y no tienen por qué
 * enterarse de que acá el fondo es oscuro. Heredan y quedan translúcidos solos.
 */
export function ContenedorAuth({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <main
      // `text-[var(--texto)]` explícito y no solo la variable: `body` ya
      // computó su `color` con el `--texto` de `:root`, y los descendientes
      // heredan ese color resuelto, no la variable. Sin esto, cualquier título
      // sin clase de color se queda con el gris del tema claro y desaparece
      // contra el vidrio.
      className="relative grid min-h-screen place-items-center overflow-hidden px-5 py-10 text-[var(--texto)]"
      style={
        {
          background: "linear-gradient(160deg, #04222c 0%, #06304a 55%, #041d2a 100%)",
          "--superficie": "rgba(255, 255, 255, 0.07)",
          "--texto": "#eef2f7",
          "--texto-secundario": "rgba(226, 232, 240, 0.72)",
          "--borde": "rgba(255, 255, 255, 0.16)",
          "--hover": "rgba(255, 255, 255, 0.10)",
          // Fondo del botón primario: lleva texto blanco encima, así que el
          // celeste claro del tema oscuro no alcanza (quedaría en 2:1).
          "--mar": "#2b5fd0",
          "--mar-oscuro": "#23509c",
          // Para enlaces sobre el vidrio, donde el azul del botón sí se pierde.
          "--enlace": "#b9d1ff",
        } as React.CSSProperties
      }
    >
      {/* Luces difusas colocadas para pasar POR DETRÁS de la tarjeta, no por
          las esquinas: `backdrop-blur` solo se nota si hay color que desenfocar
          justo detrás del vidrio. Con las luces en los bordes, la tarjeta se
          lee como un rectángulo gris. */}
      <div
        aria-hidden="true"
        className="pointer-events-none absolute left-1/2 top-1/2 size-[42rem] -translate-x-[70%] -translate-y-[65%] rounded-full opacity-60 blur-3xl"
        style={{ background: "radial-gradient(circle, #3f6fe0 0%, transparent 70%)" }}
      />
      <div
        aria-hidden="true"
        className="pointer-events-none absolute left-1/2 top-1/2 size-[34rem] -translate-x-[15%] -translate-y-[20%] rounded-full opacity-45 blur-3xl"
        style={{ background: "radial-gradient(circle, #0ea5b7 0%, transparent 70%)" }}
      />

      <div className="relative w-full max-w-md">
        <div className="mb-7 flex items-center justify-center gap-3">
          <span className="grid size-10 place-items-center rounded-lg border border-white/15 bg-white/10">
            <Image
              className="h-7 w-auto"
              src="/brand/amvarmar-isotipo.png"
              alt=""
              width={24}
              height={36}
              priority
            />
          </span>
          <div>
            <p className="text-base font-bold tracking-tight text-white">AMVARMAR</p>
            <p className="text-xs font-medium text-[var(--texto-secundario)]">Gestión logística</p>
          </div>
        </div>

        <div className="rounded-2xl border border-white/15 bg-white/[0.07] p-7 shadow-[0_24px_60px_-20px_rgba(0,0,0,0.7)] backdrop-blur-xl sm:p-9">
          {children}
        </div>
      </div>
    </main>
  );
}
