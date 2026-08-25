"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Clock3, Laptop, LogOut, MapPin, ShieldCheck, Smartphone, Trash2 } from "lucide-react";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { AvisoError } from "@/components/ui/aviso-error";
import { Boton } from "@/components/ui/boton";
import { CargandoPagina, EstadoVacio } from "@/components/ui/estados-pagina";
import { Modal } from "@/components/ui/modal";
import { useSesion } from "@/features/auth/contexto-sesion";
import { api, exigirDatos } from "@/lib/api/client";
import type { SesionActiva } from "@/lib/api/tipos";
import { formatearFechaHora } from "@/lib/utilidades";

function IconoDispositivo({ tipo }: { tipo: string }) {
  return tipo === "IOS" || tipo === "ANDROID" ? <Smartphone className="size-5" /> : <Laptop className="size-5" />;
}

export default function PaginaSesiones() {
  const [confirmarCierre, setConfirmarCierre] = useState(false);
  const queryClient = useQueryClient();
  const router = useRouter();
  const { cerrarTodasLasSesiones } = useSesion();

  const sesiones = useQuery({
    queryKey: ["sesiones"],
    queryFn: async () => exigirDatos(await api.GET("/api/v1/auth/sessions")),
  });

  const revocar = useMutation({
    mutationFn: async (sesion: SesionActiva) =>
      exigirDatos(
        await api.DELETE("/api/v1/auth/sessions/{session_id}", {
          params: { path: { session_id: sesion.id } },
        }),
      ),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["sesiones"] }),
  });

  async function cerrarTodas() {
    await cerrarTodasLasSesiones();
    router.replace("/login");
  }

  return (
    <div className="space-y-6">
      <header className="flex flex-col gap-4 border-b pb-5 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <p className="text-xs font-bold uppercase text-[var(--marca)]">Cuenta</p>
          <h1 className="mt-1 text-2xl font-bold">Sesiones activas</h1>
        </div>
        <Boton variante="peligro" onClick={() => setConfirmarCierre(true)}>
          <LogOut className="size-4" aria-hidden="true" />
          Cerrar todas
        </Boton>
      </header>

      {sesiones.isLoading ? <CargandoPagina texto="Cargando sesiones" /> : null}
      {sesiones.error ? <AvisoError error={sesiones.error} /> : null}
      {revocar.error ? <AvisoError error={revocar.error} /> : null}

      {sesiones.data?.length === 0 ? (
        <EstadoVacio titulo="No hay sesiones activas" descripcion="No se encontraron dispositivos con acceso vigente." />
      ) : (
        <div className="divide-y rounded-lg border bg-white">
          {sesiones.data?.map((sesion) => (
            <article key={sesion.id} className="flex flex-col gap-4 p-5 sm:flex-row sm:items-center">
              <span className="grid size-11 shrink-0 place-items-center rounded-md bg-[#e8f0f2] text-[var(--mar)]">
                <IconoDispositivo tipo={sesion.client_type} />
              </span>
              <div className="min-w-0 flex-1">
                <div className="flex flex-wrap items-center gap-2">
                  <h2 className="truncate text-sm font-semibold">{sesion.device_name ?? "Dispositivo sin nombre"}</h2>
                  {sesion.es_sesion_actual ? (
                    <span className="inline-flex items-center gap-1 rounded bg-[#edf8f3] px-2 py-1 text-[11px] font-bold text-[var(--exito)]">
                      <ShieldCheck className="size-3" aria-hidden="true" /> Actual
                    </span>
                  ) : null}
                </div>
                <div className="mt-2 flex flex-wrap gap-x-5 gap-y-1 text-xs text-[var(--texto-secundario)]">
                  <span className="inline-flex items-center gap-1.5"><Clock3 className="size-3.5" /> Último uso {formatearFechaHora(sesion.last_used_at)}</span>
                  <span className="inline-flex items-center gap-1.5"><MapPin className="size-3.5" /> {sesion.ip_last_used ?? "IP no disponible"}</span>
                </div>
              </div>
              {!sesion.es_sesion_actual ? (
                <button
                  type="button"
                  className="grid size-10 place-items-center self-end rounded-md text-[var(--peligro)] hover:bg-[#fff2f0] sm:self-auto"
                  onClick={() => revocar.mutate(sesion)}
                  disabled={revocar.isPending}
                  title="Revocar sesión"
                  aria-label={`Revocar sesión ${sesion.device_name ?? "sin nombre"}`}
                >
                  <Trash2 className="size-4" />
                </button>
              ) : null}
            </article>
          ))}
        </div>
      )}

      <Modal abierto={confirmarCierre} cerrar={() => setConfirmarCierre(false)} titulo="Cerrar todas las sesiones">
        <div className="space-y-5 p-5">
          <p className="text-sm leading-6 text-[var(--texto-secundario)]">Se cerrará el acceso en todos los dispositivos, incluido este.</p>
          <div className="flex justify-end gap-3">
            <Boton variante="secundario" onClick={() => setConfirmarCierre(false)}>Cancelar</Boton>
            <Boton variante="peligro" onClick={() => void cerrarTodas()}>Cerrar todas</Boton>
          </div>
        </div>
      </Modal>
    </div>
  );
}
