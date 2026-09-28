"use client";

import { Pencil, Power, PowerOff, Plus } from "lucide-react";
import { useState } from "react";
import { AvisoError } from "@/components/ui/aviso-error";
import { Boton } from "@/components/ui/boton";
import { CargandoPagina, EstadoVacio } from "@/components/ui/estados-pagina";
import { Modal } from "@/components/ui/modal";
import {
  useActivarUbicacion,
  useActualizarUbicacion,
  useCrearUbicacion,
  useDesactivarUbicacion,
  useUbicacionesAdmin,
} from "@/features/admin/consultas";
import { useSesion } from "@/features/auth/contexto-sesion";
import type { CrearUbicacion, UbicacionAdmin } from "@/lib/api/tipos";

const VACIA: CrearUbicacion = {
  country_code: "",
  city_code: "",
  location_code: "",
  name: "",
};

export default function PaginaUbicaciones() {
  const { tienePermiso } = useSesion();
  const puedeGestionar = tienePermiso("locations.manage");
  const consulta = useUbicacionesAdmin();
  const crear = useCrearUbicacion();
  const actualizar = useActualizarUbicacion();
  const activar = useActivarUbicacion();
  const desactivar = useDesactivarUbicacion();
  const [formulario, setFormulario] = useState<CrearUbicacion | null>(null);
  const [editando, setEditando] = useState<string | null>(null);
  const [porDesactivar, setPorDesactivar] = useState<UbicacionAdmin | null>(null);

  function abrirEdicion(ubicacion: UbicacionAdmin) {
    setEditando(ubicacion.id);
    setFormulario({
      country_code: ubicacion.country_code,
      city_code: ubicacion.city_code,
      location_code: ubicacion.location_code,
      name: ubicacion.name,
    });
  }

  function cerrarFormulario() {
    setFormulario(null);
    setEditando(null);
    crear.reset();
    actualizar.reset();
  }

  async function guardar() {
    if (!formulario) return;
    const datos = {
      country_code: formulario.country_code.trim().toUpperCase(),
      city_code: formulario.city_code.trim().toUpperCase(),
      location_code: formulario.location_code.trim().toUpperCase(),
      name: formulario.name.trim(),
    };
    if (editando) await actualizar.mutateAsync({ id: editando, ...datos });
    else await crear.mutateAsync(datos);
    cerrarFormulario();
  }

  if (!puedeGestionar) {
    return (
      <AvisoError error={new Error("No tiene permiso para administrar ubicaciones.")} />
    );
  }

  return (
    <div className="space-y-6">
      <header className="flex items-end justify-between gap-4">
        <div>
          <p className="text-xs font-bold uppercase text-[var(--marca)]">Administración</p>
          <h1 className="mt-1 text-2xl font-bold">Ubicaciones</h1>
          <p className="mt-1 text-sm text-[var(--texto-secundario)]">
            Orígenes y destinos disponibles para las cargas.
          </p>
        </div>
        <Boton onClick={() => setFormulario({ ...VACIA })}>
          <Plus className="size-4" aria-hidden="true" /> Nueva ubicación
        </Boton>
      </header>

      {consulta.isLoading ? <CargandoPagina texto="Cargando ubicaciones" /> : null}
      {consulta.error ? <AvisoError error={consulta.error} /> : null}
      {consulta.data?.length === 0 ? (
        <EstadoVacio titulo="No hay ubicaciones" descripcion="Creá la primera ubicación operativa." />
      ) : null}

      {consulta.data?.length ? (
        <div className="overflow-x-auto rounded-lg border bg-[var(--superficie)]">
          <table className="w-full text-left text-sm">
            <thead className="border-b bg-[var(--hover)] text-xs uppercase text-[var(--texto-secundario)]">
              <tr>
                <th className="px-4 py-3">Ubicación</th>
                <th className="px-4 py-3">País</th>
                <th className="px-4 py-3">Ciudad</th>
                <th className="px-4 py-3">Estado</th>
                <th className="px-4 py-3 text-right">Acciones</th>
              </tr>
            </thead>
            <tbody className="divide-y">
              {consulta.data.map((ubicacion) => (
                <tr key={ubicacion.id} className={!ubicacion.is_active ? "opacity-60" : ""}>
                  <td className="px-4 py-3">
                    <strong>{ubicacion.name}</strong>
                    <span className="ml-2 font-mono text-xs text-[var(--texto-secundario)]">
                      {ubicacion.location_code}
                    </span>
                  </td>
                  <td className="px-4 py-3">{ubicacion.country_code}</td>
                  <td className="px-4 py-3">{ubicacion.city_code}</td>
                  <td className="px-4 py-3">{ubicacion.is_active ? "Activa" : "Inactiva"}</td>
                  <td className="px-4 py-3">
                    <div className="flex justify-end gap-1">
                      <button
                        className="grid size-9 place-items-center rounded-md hover:bg-[var(--hover)]"
                        onClick={() => abrirEdicion(ubicacion)}
                        aria-label={`Editar ${ubicacion.name}`}
                      >
                        <Pencil className="size-4" />
                      </button>
                      {ubicacion.is_active ? (
                        <button
                          className="grid size-9 place-items-center rounded-md text-[var(--peligro)] hover:bg-[var(--peligro-tenue)]"
                          onClick={() => setPorDesactivar(ubicacion)}
                          aria-label={`Desactivar ${ubicacion.name}`}
                        >
                          <PowerOff className="size-4" />
                        </button>
                      ) : (
                        <button
                          className="grid size-9 place-items-center rounded-md text-[var(--marca)] hover:bg-[var(--marca-tenue)]"
                          onClick={() => activar.mutate(ubicacion.id)}
                          aria-label={`Activar ${ubicacion.name}`}
                          disabled={activar.isPending}
                        >
                          <Power className="size-4" />
                        </button>
                      )}
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
      {activar.error ? <AvisoError error={activar.error} /> : null}

      <Modal abierto={formulario !== null} cerrar={cerrarFormulario} titulo={editando ? "Editar ubicación" : "Nueva ubicación"}>
        <div className="space-y-4 p-5">
          {(["name", "country_code", "city_code", "location_code"] as const).map((campo) => {
            const etiquetas = { name: "Nombre", country_code: "País (2 letras)", city_code: "Código de ciudad", location_code: "Código de ubicación" };
            return (
              <label key={campo} className="block">
                <span className="mb-1 block text-sm font-medium">{etiquetas[campo]}</span>
                <input
                  className="w-full rounded-md border bg-[var(--superficie)] px-3 py-2 text-sm"
                  value={formulario?.[campo] ?? ""}
                  onChange={(evento) => setFormulario((actual) => actual ? { ...actual, [campo]: campo === "name" ? evento.target.value : evento.target.value.toUpperCase() } : actual)}
                  placeholder={campo === "location_code" ? "CN-SHA" : campo === "city_code" ? "SHA" : campo === "country_code" ? "CN" : "Shanghai"}
                />
              </label>
            );
          })}
          {crear.error || actualizar.error ? <AvisoError error={crear.error ?? actualizar.error} /> : null}
          <div className="flex justify-end gap-2">
            <Boton variante="secundario" onClick={cerrarFormulario}>Cancelar</Boton>
            <Boton cargando={crear.isPending || actualizar.isPending} disabled={!formulario?.name.trim()} onClick={() => void guardar()}>Guardar</Boton>
          </div>
        </div>
      </Modal>

      <Modal abierto={porDesactivar !== null} cerrar={() => setPorDesactivar(null)} titulo="Desactivar ubicación">
        <div className="space-y-4 p-5">
          <p className="text-sm">¿Desactivar <strong>{porDesactivar?.name}</strong>? Ya no aparecerá al crear cargas.</p>
          {desactivar.error ? <AvisoError error={desactivar.error} /> : null}
          <div className="flex justify-end gap-2">
            <Boton variante="secundario" onClick={() => setPorDesactivar(null)}>Cancelar</Boton>
            <Boton cargando={desactivar.isPending} onClick={async () => { if (!porDesactivar) return; await desactivar.mutateAsync(porDesactivar.id); setPorDesactivar(null); }}>Desactivar</Boton>
          </div>
        </div>
      </Modal>
    </div>
  );
}
