"use client";

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ErrorApi, exigirDatos } from "@/lib/api/client";
import type { components } from "@/lib/api/generated";
import type { Expediente } from "@/lib/api/tipos";

type IssuedBy = components["schemas"]["IssuedBy"];
export type TrabajoExportacion = components["schemas"]["ExportJobResponse"];

export const claveExpediente = (cargaId: string) => ["expediente", cargaId] as const;

export function useExpediente(cargaId: string) {
  return useQuery({
    queryKey: claveExpediente(cargaId),
    queryFn: async (): Promise<Expediente> =>
      exigirDatos(
        await api.GET("/api/v1/shipments/{shipment_id}/documents", {
          params: { path: { shipment_id: cargaId } },
        }),
      ),
    enabled: Boolean(cargaId),
    refetchInterval: (consulta) =>
      consulta.state.data?.documentos.some((d) => d.upload_status === "PROCESSING")
        ? 1500
        : false,
  });
}

function subirAStorage(
  url: string,
  archivo: File,
  onProgress?: (porcentaje: number) => void,
): Promise<void> {
  return new Promise((resolve, reject) => {
    const solicitud = new XMLHttpRequest();
    solicitud.open("PUT", url);
    solicitud.upload.addEventListener("progress", (evento) => {
      if (evento.lengthComputable) {
        onProgress?.(Math.round((evento.loaded / evento.total) * 100));
      }
    });
    solicitud.addEventListener("load", () => {
      if (solicitud.status >= 200 && solicitud.status < 300) {
        onProgress?.(100);
        resolve();
      } else {
        reject(
          new ErrorApi("No se pudo subir el archivo al almacenamiento.", {
            status: solicitud.status,
            code: "SUBIDA_FALLIDA",
          }),
        );
      }
    });
    solicitud.addEventListener("error", () =>
      reject(
        new ErrorApi("No se pudo conectar con el almacenamiento.", {
          status: 503,
          code: "SUBIDA_FALLIDA",
        }),
      ),
    );
    solicitud.send(archivo);
  });
}

interface SubirDocumentoArgs {
  archivo: File;
  tipoId: string;
  issuedBy: IssuedBy;
  onProgress?: (porcentaje: number) => void;
}

export function useSubirDocumento(cargaId: string) {
  const cliente = useQueryClient();

  return useMutation({
    mutationFn: async ({ archivo, tipoId, issuedBy, onProgress }: SubirDocumentoArgs) => {
      const reserva = exigirDatos(
        await api.POST("/api/v1/shipments/{shipment_id}/documents/presign", {
          params: { path: { shipment_id: cargaId } },
          body: {
            document_type_id: tipoId,
            issued_by: issuedBy,
            original_name: archivo.name,
          },
        }),
      );

      if (archivo.size > reserva.max_bytes) {
        throw new ErrorApi(
          `El archivo pesa ${formatearTamano(archivo.size)} y el máximo es ${formatearTamano(reserva.max_bytes)}.`,
          { status: 413, code: "ARCHIVO_MUY_GRANDE" },
        );
      }

      await subirAStorage(reserva.upload_url, archivo, onProgress);
      return exigirDatos(
        await api.POST("/api/v1/shipments/{shipment_id}/documents/complete", {
          params: { path: { shipment_id: cargaId } },
          body: { document_id: reserva.document_id },
        }),
      );
    },
    onSuccess: () => {
      cliente.invalidateQueries({ queryKey: claveExpediente(cargaId) });
      cliente.invalidateQueries({ queryKey: ["carga", cargaId] });
      cliente.invalidateQueries({ queryKey: ["cargas"] });
    },
  });
}

export function useRenombrarDocumento(cargaId: string) {
  const cliente = useQueryClient();
  return useMutation({
    mutationFn: async ({ id, nombre }: { id: string; nombre: string }) =>
      exigirDatos(
        await api.PATCH("/api/v1/documents/{document_id}", {
          params: { path: { document_id: id } },
          body: { original_name: nombre },
        }),
      ),
    onSuccess: () => cliente.invalidateQueries({ queryKey: claveExpediente(cargaId) }),
  });
}

export function useQuitarDocumento(cargaId: string) {
  const cliente = useQueryClient();
  return useMutation({
    mutationFn: async ({ id, motivo }: { id: string; motivo: string }) => {
      const resultado = await api.DELETE("/api/v1/documents/{document_id}", {
        params: { path: { document_id: id }, query: { motivo } },
      });
      if (resultado.error) throw resultado.error;
    },
    onSuccess: () => {
      cliente.invalidateQueries({ queryKey: claveExpediente(cargaId) });
      cliente.invalidateQueries({ queryKey: ["carga", cargaId] });
      cliente.invalidateQueries({ queryKey: ["cargas"] });
    },
  });
}

export function useDescargar() {
  return useMutation({
    mutationFn: async (documentoId: string) =>
      exigirDatos(
        await api.GET("/api/v1/documents/{document_id}/download", {
          params: { path: { document_id: documentoId } },
        }),
      ),
  });
}

export function formatearTamano(bytes: number) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  return `${(bytes / (1024 * 1024 * 1024)).toFixed(1)} GB`;
}

type RecursoExportacion =
  | { contexto: "SHIPMENT"; id: string }
  | { contexto: "DISPATCH"; id: string };

function useExportacion(recurso: RecursoExportacion) {
  const cliente = useQueryClient();
  const claveLocal = `document-export:${recurso.contexto}:${recurso.id}`;
  const [jobId, setJobId] = useState<string | null>(() =>
    typeof window === "undefined" ? null : window.localStorage.getItem(claveLocal),
  );

  const solicitud = useMutation({
    mutationFn: async (): Promise<TrabajoExportacion> => {
      if (recurso.contexto === "SHIPMENT") {
        return exigirDatos(
          await api.POST("/api/v1/shipments/{shipment_id}/documents/exports", {
            params: { path: { shipment_id: recurso.id } },
          }),
        );
      }
      return exigirDatos(
        await api.POST("/api/v1/dispatch-requests/{dispatch_id}/documents/exports", {
          params: { path: { dispatch_id: recurso.id } },
          body: { kind: "BLS" },
        }),
      );
    },
    onSuccess: (job) => {
      window.localStorage.setItem(claveLocal, job.id);
      setJobId(job.id);
      cliente.setQueryData(["exportacion-documental", job.id], job);
    },
  });

  const estado = useQuery({
    queryKey: ["exportacion-documental", jobId],
    queryFn: async (): Promise<TrabajoExportacion> => {
      try {
        return exigirDatos(
          await api.GET("/api/v1/document-export-jobs/{job_id}", {
            params: { path: { job_id: jobId as string } },
          }),
        );
      } catch (error) {
        if (error instanceof ErrorApi && error.status === 404) {
          window.localStorage.removeItem(claveLocal);
        }
        throw error;
      }
    },
    enabled: Boolean(jobId),
    retry: false,
    refetchInterval: (consulta) =>
      ["PENDING", "PROCESSING"].includes(consulta.state.data?.status ?? "")
        ? 1500
        : false,
  });

  const trabajoNoEncontrado = estado.error instanceof ErrorApi && estado.error.status === 404;

  const descargar = useMutation({
    mutationFn: async () => {
      if (!jobId) return;
      const enlace = exigirDatos(
        await api.GET("/api/v1/document-export-jobs/{job_id}/download", {
          params: { path: { job_id: jobId } },
        }),
      );
      const elemento = document.createElement("a");
      elemento.href = enlace.url;
      elemento.download = enlace.filename;
      elemento.rel = "noopener noreferrer";
      elemento.click();
    },
  });

  return {
    job: trabajoNoEncontrado ? null : (estado.data ?? solicitud.data ?? null),
    solicitar: solicitud.mutate,
    descargar: descargar.mutate,
    preparando:
      solicitud.isPending || ["PENDING", "PROCESSING"].includes(estado.data?.status ?? ""),
    descargando: descargar.isPending,
    error: solicitud.error ?? (trabajoNoEncontrado ? null : estado.error) ?? descargar.error,
  };
}

export function useExportacionCarga(cargaId: string) {
  return useExportacion({ contexto: "SHIPMENT", id: cargaId });
}

export function useExportacionBls(dispatchId: string) {
  return useExportacion({ contexto: "DISPATCH", id: dispatchId });
}

export const claveDocsDespacho = (id: string) => ["despacho", id, "documentos"] as const;

export function useDocumentosDeDespacho(dispatchId: string) {
  return useQuery({
    queryKey: claveDocsDespacho(dispatchId),
    queryFn: async () =>
      exigirDatos(
        await api.GET("/api/v1/dispatch-requests/{dispatch_id}/documents", {
          params: { path: { dispatch_id: dispatchId } },
        }),
      ),
    enabled: Boolean(dispatchId),
    refetchInterval: (consulta) =>
      consulta.state.data?.some((d) => d.upload_status === "PROCESSING") ? 1500 : false,
  });
}

export function useSubirDocumentoDeDespacho(dispatchId: string) {
  const cliente = useQueryClient();

  return useMutation({
    mutationFn: async ({ archivo, tipoId, issuedBy, onProgress }: SubirDocumentoArgs) => {
      const reserva = exigirDatos(
        await api.POST("/api/v1/dispatch-requests/{dispatch_id}/documents/presign", {
          params: { path: { dispatch_id: dispatchId } },
          body: {
            document_type_id: tipoId,
            issued_by: issuedBy,
            original_name: archivo.name,
          },
        }),
      );
      if (archivo.size > reserva.max_bytes) {
        throw new ErrorApi(
          `El archivo pesa ${formatearTamano(archivo.size)} y el máximo es ${formatearTamano(reserva.max_bytes)}.`,
          { status: 413, code: "ARCHIVO_MUY_GRANDE" },
        );
      }
      await subirAStorage(reserva.upload_url, archivo, onProgress);
      return exigirDatos(
        await api.POST("/api/v1/dispatch-requests/{dispatch_id}/documents/complete", {
          params: { path: { dispatch_id: dispatchId } },
          body: { document_id: reserva.document_id },
        }),
      );
    },
    onSuccess: () => cliente.invalidateQueries({ queryKey: claveDocsDespacho(dispatchId) }),
  });
}
