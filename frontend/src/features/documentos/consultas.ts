"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ErrorApi, exigirDatos } from "@/lib/api/client";
import type { Expediente } from "@/lib/api/tipos";

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
  });
}

/**
 * Sube un archivo en los tres tiempos que exige el backend.
 *
 * 1. `presign` reserva el documento y firma la URL.
 * 2. El archivo va DIRECTO al storage, sin pasar por la aplicación: un PDF de
 *    200 MB no tiene por qué atravesar el servidor de la API.
 * 3. `complete` verifica lo que realmente llegó — tipo, tamaño y hash se miden
 *    sobre los bytes almacenados, no sobre lo que el navegador declaró.
 *
 * Si el paso 2 falla, no se llama a `complete`: el documento queda a medias en
 * el servidor y se puede reintentar, en vez de darse por bueno.
 */
export function useSubirDocumento(cargaId: string) {
  const cliente = useQueryClient();

  return useMutation({
    mutationFn: async ({ archivo, tipoId }: { archivo: File; tipoId: string }) => {
      const reserva = exigirDatos(
        await api.POST("/api/v1/shipments/{shipment_id}/documents/presign", {
          params: { path: { shipment_id: cargaId } },
          body: { document_type_id: tipoId, original_name: archivo.name },
        }),
      );

      if (archivo.size > reserva.max_bytes) {
        throw new ErrorApi(
          `El archivo pesa ${formatearTamano(archivo.size)} y el máximo es ${formatearTamano(reserva.max_bytes)}.`,
          { status: 413, code: "ARCHIVO_MUY_GRANDE" },
        );
      }

      // Sin cabeceras propias: la URL se firmó solo sobre el destino, así que
      // agregar `Content-Type` invalidaría la firma.
      const subida = await fetch(reserva.upload_url, { method: "PUT", body: archivo });
      if (!subida.ok) {
        throw new ErrorApi("No se pudo subir el archivo al almacenamiento.", {
          status: subida.status,
          code: "SUBIDA_FALLIDA",
        });
      }

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
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}
