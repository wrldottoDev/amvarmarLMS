import Image from "next/image";

export function ContenedorAuth({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <main className="grid min-h-screen bg-[var(--superficie)] lg:grid-cols-[minmax(340px,0.85fr)_1.15fr]">
      <section className="flex min-h-screen items-center justify-center px-6 py-10 sm:px-10 lg:min-h-0">
        <div className="w-full max-w-sm">
          <div className="mb-10 flex items-center gap-3">
            <span className="grid size-11 place-items-center rounded-md bg-[var(--marca-tenue)]">
              <Image className="h-9 w-auto" src="/brand/amvarmar-isotipo.png" alt="" width={24} height={36} priority />
            </span>
            <div>
              <p className="text-lg font-bold text-[var(--mar-oscuro)]">AMVARMAR</p>
              <p className="text-xs font-medium text-[var(--texto-secundario)]">Gestión logística</p>
            </div>
          </div>
          {children}
        </div>
      </section>

      <aside className="relative hidden overflow-hidden bg-[var(--mar-oscuro)] lg:block" aria-hidden="true">
        <div className="absolute inset-x-0 top-0 h-2 bg-[var(--marca)]" />
        <div className="absolute inset-0 grid place-items-center p-16">
          <div className="relative h-[420px] w-full max-w-xl border-y border-white/15">
            <div className="absolute left-0 top-20 h-px w-full bg-white/10" />
            <div className="absolute left-0 top-1/2 h-px w-full bg-white/10" />
            <div className="absolute bottom-20 left-0 h-px w-full bg-white/10" />
            <div className="absolute left-[16%] top-14 size-3 rounded-full bg-[var(--marca)]" />
            <div className="absolute left-[16%] top-[62px] h-[120px] w-px bg-[var(--marca)]" />
            <div className="absolute left-[16%] top-[174px] h-px w-[46%] bg-[var(--marca)]" />
            <div className="absolute left-[62%] top-[169px] size-3 rounded-full bg-[var(--marca)]" />
            <div className="absolute left-[62%] top-[181px] h-[142px] w-px bg-[var(--marca)]" />
            <div className="absolute bottom-[91px] left-[62%] h-px w-[25%] bg-[var(--marca)]" />
            <div className="absolute bottom-[86px] right-[12%] size-3 rounded-full bg-[var(--marca)]" />
            <Image
              className="absolute bottom-8 right-0 h-auto w-32 opacity-90"
              src="/brand/amvarmar-isotipo.png"
              alt=""
              width={102}
              height={150}
            />
          </div>
        </div>
      </aside>
    </main>
  );
}
