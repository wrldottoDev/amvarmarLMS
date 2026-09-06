"use client";

import { KeyRound, Plus, Power, UserRound } from "lucide-react";
import { useState } from "react";
import { ContrasenaTemporal, type EstadoInvitacion } from "./contrasena-temporal";
import { AvisoError } from "@/components/ui/aviso-error";
import { Boton } from "@/components/ui/boton";
import { CargandoPagina } from "@/components/ui/estados-pagina";
import { Modal } from "@/components/ui/modal";
import {
  useCrearUsuario,
  useDesactivarUsuario,
  useEmpresas,
  useRestablecerContrasena,
  useUsuarios,
} from "@/features/admin/consultas";
import { etiquetaEstadoCuenta, etiquetaRol, rolesDeCliente, rolesInternos } from "@/features/admin/roles";
import { clases, tiempoRelativo } from "@/lib/utilidades";

/**
 * Alta gestión (Operaciones/Administración, `users.create_internal` +
 * `users.manage` global): elige cualquier empresa y puede dar de alta
 * personal interno. `empresaFiltro` es opcional — llega por `?empresa=`
 * cuando se entra desde la ficha de una empresa puntual.
 *
 * Alcance propio (un `CLIENT_ADMIN`/`CLIENT_USER`, `users.manage` con
 * alcance a SU empresa — ADR-0004, los dos roles de cliente comparten
 * exactamente el mismo permiso): la empresa es siempre la propia, nunca un
 * selector, y el rol ofrecido nunca es personal interno — el backend lo
 * rechazaría igual (`USERS_CREATE_INTERNAL`), pero no tiene sentido
 * ofrecerlo.
 */
type Modo =
  | { alcance: "completo"; empresaFiltro?: string }
  | { alcance: "propia"; empresaId: string; nombreEmpresa: string };

export function GestionUsuarios({ modo }: { modo: Modo }) {
  const esCompleto = modo.alcance === "completo";

  const [creando, setCreando] = useState(false);
  const [aDesactivar, setADesactivar] = useState<{ id: string; nombre: string } | null>(null);
  const [credenciales, setCredenciales] = useState<{
    correo: string;
    clave: string;
    invitacion: EstadoInvitacion;
  } | null>(null);

  const { data, isPending, error } = useUsuarios(
    esCompleto ? modo.empresaFiltro : modo.empresaId,
  );
  const empresas = useEmpresas(false, esCompleto);
  const crear = useCrearUsuario();
  const restablecer = useRestablecerContrasena();
  const desactivar = useDesactivarUsuario();

  const [correo, setCorreo] = useState("");
  const [nombre, setNombre] = useState("");
  const [apellido, setApellido] = useState("");
  const [telefono, setTelefono] = useState("");
  const [rol, setRol] = useState<string>("CLIENT_USER");
  const [empresa, setEmpresa] = useState<string>(esCompleto ? (modo.empresaFiltro ?? "") : "");

  const esInterno = esCompleto && rolesInternos.some((r) => r.codigo === rol);

  async function guardar() {
    const creado = await crear.mutateAsync({
      email: correo.trim(),
      first_name: nombre.trim(),
      last_name: apellido.trim(),
      role_code: rol,
      // El personal interno no lleva empresa: su alcance es global (ADR-0011).
      company_id: esInterno ? null : esCompleto ? empresa || null : modo.empresaId,
      phone: telefono.trim() || null,
    });
    setCredenciales({
      correo: creado.email,
      clave: creado.password_temporal,
      invitacion: creado.invitacion_enviada ? "enviada" : "fallo",
    });
    setCreando(false);
    setCorreo("");
    setNombre("");
    setApellido("");
    setTelefono("");
    setRol("CLIENT_USER");
  }

  const nombreEmpresaFiltro = esCompleto
    ? empresas.data?.find((e) => e.id === modo.empresaFiltro)
    : undefined;
  const titulo = esCompleto
    ? nombreEmpresaFiltro
      ? `Usuarios de ${nombreEmpresaFiltro.trade_name || nombreEmpresaFiltro.legal_name}`
      : "Usuarios"
    : `Usuarios de ${modo.nombreEmpresa}`;
  const descripcionTitulo = esCompleto
    ? "Quiénes pueden entrar al sistema y con qué permisos."
    : "Quiénes de tu empresa pueden entrar al sistema.";

  return (
    <section className="space-y-5">
      <header className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-xl font-bold">{titulo}</h1>
          <p className="mt-0.5 text-sm text-[var(--texto-secundario)]">{descripcionTitulo}</p>
        </div>
        <Boton
          onClick={() => {
            if (esCompleto) setEmpresa(modo.empresaFiltro ?? "");
            setCreando(true);
          }}
        >
          <Plus className="size-4" aria-hidden="true" />
          Nuevo usuario
        </Boton>
      </header>

      {credenciales ? (
        <ContrasenaTemporal
          correo={credenciales.correo}
          contrasena={credenciales.clave}
          invitacion={credenciales.invitacion}
        />
      ) : null}

      {error ? <AvisoError error={error} /> : null}
      {isPending ? <CargandoPagina /> : null}

      {data && data.length > 0 ? (
        <ul className="divide-y overflow-hidden rounded-md border bg-[var(--superficie)]">
          {data.map((usuario) => (
            <li
              key={usuario.id}
              className={clases(
                "flex flex-wrap items-center gap-4 px-4 py-4",
                usuario.status !== "ACTIVE" && "opacity-70",
              )}
            >
              <span className="grid size-9 shrink-0 place-items-center rounded-md bg-[var(--marca-tenue)] text-[var(--mar)]">
                <UserRound className="size-4" aria-hidden="true" />
              </span>

              <span className="min-w-0 flex-1">
                <strong className="block text-sm">
                  {usuario.first_name} {usuario.last_name}
                </strong>
                <span className="block truncate text-xs text-[var(--texto-secundario)]">
                  {usuario.email}
                  {esCompleto
                    ? usuario.company_name
                      ? ` · ${usuario.company_name}`
                      : " · Personal interno"
                    : ""}
                </span>
              </span>

              <span className="shrink-0 rounded-full border bg-[var(--hover)] px-2.5 py-0.5 text-xs font-medium">
                {etiquetaRol[usuario.role_code ?? ""] ?? usuario.role_code ?? "Sin rol"}
              </span>

              <span className="hidden w-32 shrink-0 text-xs text-[var(--texto-secundario)] sm:block">
                {usuario.last_login_at ? `Entró ${tiempoRelativo(usuario.last_login_at)}` : "Nunca entró"}
              </span>

              {usuario.status !== "ACTIVE" ? (
                <span className="shrink-0 rounded-full border border-[var(--peligro-borde)] bg-[var(--peligro-tenue)] px-2.5 py-0.5 text-xs font-semibold text-[var(--peligro)]">
                  {etiquetaEstadoCuenta[usuario.status] ?? usuario.status}
                </span>
              ) : null}

              <span className="flex shrink-0 gap-2">
                <button
                  type="button"
                  className="flex h-9 items-center gap-1.5 rounded-md border px-3 text-sm font-medium hover:bg-[var(--hover)]"
                  title="Genera una contraseña temporal y cierra sus sesiones abiertas"
                  onClick={async () => {
                    const resultado = await restablecer.mutateAsync(usuario.id);
                    setCredenciales({
                      correo: usuario.email,
                      clave: resultado.password_temporal,
                      // Un restablecimiento no manda invitación: la temporal es
                      // la única vía y por eso se muestra abierta.
                      invitacion: "no-aplica",
                    });
                  }}
                  disabled={restablecer.isPending}
                >
                  <KeyRound className="size-4" aria-hidden="true" />
                  Contraseña
                </button>

                {usuario.status === "ACTIVE" ? (
                  <button
                    type="button"
                    className="flex h-9 items-center gap-1.5 rounded-md border px-3 text-sm font-medium text-[var(--peligro)] hover:bg-[var(--peligro-tenue)]"
                    onClick={() =>
                      setADesactivar({
                        id: usuario.id,
                        nombre: `${usuario.first_name} ${usuario.last_name}`,
                      })
                    }
                  >
                    <Power className="size-4" aria-hidden="true" />
                    Desactivar
                  </button>
                ) : null}
              </span>
            </li>
          ))}
        </ul>
      ) : null}

      {data && data.length === 0 ? (
        <div className="rounded-md border bg-[var(--superficie)] px-6 py-16 text-center">
          <UserRound className="mx-auto size-8 text-[var(--texto-secundario)]" aria-hidden="true" />
          <p className="mt-3 text-sm font-medium">No hay usuarios que mostrar.</p>
        </div>
      ) : null}

      <Modal abierto={creando} titulo="Nuevo usuario" cerrar={() => setCreando(false)}>
        <div className="space-y-3">
          <div className="grid gap-3 sm:grid-cols-2">
            <label className="block">
              <span className="mb-1 block text-sm font-medium">Nombre</span>
              <input
                className="w-full rounded-md border px-3 py-2 text-sm"
                value={nombre}
                onChange={(evento) => setNombre(evento.target.value)}
              />
            </label>
            <label className="block">
              <span className="mb-1 block text-sm font-medium">Apellido</span>
              <input
                className="w-full rounded-md border px-3 py-2 text-sm"
                value={apellido}
                onChange={(evento) => setApellido(evento.target.value)}
              />
            </label>
          </div>

          <label className="block">
            <span className="mb-1 block text-sm font-medium">Correo</span>
            <input
              type="email"
              className="w-full rounded-md border px-3 py-2 text-sm"
              value={correo}
              onChange={(evento) => setCorreo(evento.target.value)}
              placeholder="persona@empresa.com"
            />
            <span className="mt-1 block text-xs text-[var(--texto-secundario)]">
              Con este correo entra al sistema.
            </span>
          </label>

          <label className="block">
            <span className="mb-1 block text-sm font-medium">
              Teléfono <span className="font-normal text-[var(--texto-secundario)]">(opcional)</span>
            </span>
            <input
              className="w-full rounded-md border px-3 py-2 text-sm"
              value={telefono}
              onChange={(evento) => setTelefono(evento.target.value)}
            />
          </label>

          <fieldset>
            <legend className="mb-1.5 text-sm font-medium">¿Qué va a poder hacer?</legend>
            <div className="space-y-1.5">
              {(esCompleto ? [...rolesDeCliente, ...rolesInternos] : rolesDeCliente).map(
                (opcion) => (
                  <label
                    key={opcion.codigo}
                    className={clases(
                      "flex cursor-pointer gap-2 rounded-md border px-3 py-2.5",
                      rol === opcion.codigo
                        ? "border-[var(--mar)] bg-[var(--marca-tenue)]"
                        : "hover:bg-[var(--hover)]",
                    )}
                  >
                    <input
                      type="radio"
                      name="rol"
                      className="mt-0.5 size-4 accent-[var(--mar)]"
                      checked={rol === opcion.codigo}
                      onChange={() => setRol(opcion.codigo)}
                    />
                    <span>
                      <strong className="block text-sm">{opcion.etiqueta}</strong>
                      <span className="block text-xs text-[var(--texto-secundario)]">
                        {opcion.descripcion}
                      </span>
                    </span>
                  </label>
                ),
              )}
            </div>
          </fieldset>

          {esCompleto ? (
            esInterno ? (
              <p className="rounded-md border border-[var(--marca)] bg-[var(--marca-tenue)] px-3 py-2.5 text-sm text-[var(--mar)]">
                El personal interno no pertenece a ninguna empresa: ve todas.
              </p>
            ) : (
              <label className="block">
                <span className="mb-1 block text-sm font-medium">¿De qué empresa?</span>
                <select
                  className="w-full rounded-md border px-3 py-2 text-sm"
                  value={empresa}
                  onChange={(evento) => setEmpresa(evento.target.value)}
                >
                  <option value="">Elegí una empresa…</option>
                  {empresas.data?.map((e) => (
                    <option key={e.id} value={e.id}>
                      {e.trade_name || e.legal_name}
                    </option>
                  ))}
                </select>
              </label>
            )
          ) : null}

          {crear.error ? <AvisoError error={crear.error} /> : null}

          <div className="flex justify-end gap-2 pt-1">
            <button
              type="button"
              className="h-10 rounded-md border px-4 text-sm font-medium hover:bg-[var(--hover)]"
              onClick={() => setCreando(false)}
            >
              Cancelar
            </button>
            <Boton
              onClick={() => void guardar()}
              cargando={crear.isPending}
              disabled={
                !correo.trim() ||
                !nombre.trim() ||
                !apellido.trim() ||
                (esCompleto && !esInterno && !empresa)
              }
            >
              Crear usuario
            </Boton>
          </div>
        </div>
      </Modal>

      <Modal
        abierto={aDesactivar !== null}
        titulo={`¿Desactivar a ${aDesactivar?.nombre ?? ""}?`}
        cerrar={() => setADesactivar(null)}
      >
        <div className="space-y-3 text-sm">
          <p>
            No va a poder entrar y se cierran sus sesiones abiertas. Lo que hizo hasta ahora
            —cargas creadas, documentos subidos— <strong>se conserva</strong>.
          </p>
          {desactivar.error ? <AvisoError error={desactivar.error} /> : null}
          <div className="flex justify-end gap-2 pt-1">
            <button
              type="button"
              className="h-10 rounded-md border px-4 text-sm font-medium hover:bg-[var(--hover)]"
              onClick={() => setADesactivar(null)}
            >
              No, volver
            </button>
            <Boton
              variante="peligro"
              cargando={desactivar.isPending}
              onClick={async () => {
                if (aDesactivar) await desactivar.mutateAsync(aDesactivar.id);
                setADesactivar(null);
              }}
            >
              Sí, desactivar
            </Boton>
          </div>
        </div>
      </Modal>
    </section>
  );
}
