"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, convertirErrorApi, ErrorApi, exigirDatos } from "@/lib/api/client";
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

/**
 * Corrige el nombre visible de un documento.
 *
 * Es la acción `rename` de `edit_files` del sistema viejo. Solo personal
 * interno: el nombre es cómo Operaciones y el agente aduanal encuentran el
 * papel en el expediente.
 */
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
    onSuccess: () => cliente.invalidateQueries({ queryKey: ["expediente", cargaId] }),
  });
}

/**
 * Saca un documento del expediente.
 *
 * El archivo NO se borra del storage. Si el documento satisfacía un requisito y
 * no queda otro de su tipo, ese requisito vuelve a pendiente, así que se
 * invalida también el detalle de la carga: su contador de pendientes cambió.
 */
export function useQuitarDocumento(cargaId: string) {
  const cliente = useQueryClient();
  return useMutation({
    mutationFn: async (id: string) => {
      const resultado = await api.DELETE("/api/v1/documents/{document_id}", {
        params: { path: { document_id: id } },
      });
      if (resultado.error) throw resultado.error;
    },
    onSuccess: () => {
      cliente.invalidateQueries({ queryKey: ["expediente", cargaId] });
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

/**
 * Descarga todos los documentos de una carga en un ZIP.
 *
 * A diferencia de la descarga individual, el archivo viene por la aplicación y
 * no por una URL firmada: hay que leer cada objeto para comprimirlo, y no se
 * puede firmar algo que todavía no existe.
 */
export function useDescargarTodos(cargaId: string) {
  return useMutation({
    mutationFn: async () => {
      const respuesta = await api.GET("/api/v1/shipments/{shipment_id}/documents/download-all", {
        params: { path: { shipment_id: cargaId } },
        parseAs: "blob",
      });

      if (respuesta.error) {
        throw convertirErrorApi(respuesta.error, respuesta.response);
      }

      const nombre =
        respuesta.response.headers
          .get("content-disposition")
          ?.match(/filename="?([^"]+)"?/)?.[1] ?? "documentos.zip";

      // Se descarga creando un enlace temporal: no hay forma de que el
      // navegador guarde un blob sin uno.
      const url = URL.createObjectURL(respuesta.data as Blob);
      const enlace = document.createElement("a");
      enlace.href = url;
      enlace.download = nombre;
      enlace.click();
      URL.revokeObjectURL(url);
    },
  });
}

// --- Documentos que cuelgan del despacho, no de una carga suelta ---

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
  });
}

export function useSubirDocumentoDeDespacho(dispatchId: string) {
  const cliente = useQueryClient();

  return useMutation({
    mutationFn: async ({ archivo, tipoId }: { archivo: File; tipoId: string }) => {
      const reserva = exigirDatos(
        await api.POST("/api/v1/dispatch-requests/{dispatch_id}/documents/presign", {
          params: { path: { dispatch_id: dispatchId } },
          body: { document_type_id: tipoId, original_name: archivo.name },
        }),
      );

      if (archivo.size > reserva.max_bytes) {
        throw new ErrorApi(
          `El archivo pesa ${formatearTamano(archivo.size)} y el máximo es ${formatearTamano(reserva.max_bytes)}.`,
          { status: 413, code: "ARCHIVO_MUY_GRANDE" },
        );
      }

      const subida = await fetch(reserva.upload_url, { method: "PUT", body: archivo });
      if (!subida.ok) {
        throw new ErrorApi("No se pudo subir el archivo al almacenamiento.", {
          status: subida.status,
          code: "SUBIDA_FALLIDA",
        });
      }

      // `complete` es el de la carga: el documento vive ahí y el despacho solo
      // lo referencia. Un segundo endpoint duplicaría la verificación de bytes.
      return exigirDatos(
        await api.POST("/api/v1/shipments/{shipment_id}/documents/complete", {
          params: { path: { shipment_id: reserva.shipment_id } },
          body: { document_id: reserva.document_id },
        }),
      );
    },
    onSuccess: () => cliente.invalidateQueries({ queryKey: claveDocsDespacho(dispatchId) }),
  });
}

export function useDescargarBls(dispatchId: string) {
  return useMutation({
    mutationFn: async () => {
      const respuesta = await api.GET("/api/v1/dispatch-requests/{dispatch_id}/documents/bls", {
        params: { path: { dispatch_id: dispatchId } },
        parseAs: "blob",
      });

      if (respuesta.error) throw convertirErrorApi(respuesta.error, respuesta.response);

      const nombre =
        respuesta.response.headers
          .get("content-disposition")
          ?.match(/filename="?([^"]+)"?/)?.[1] ?? "bls.zip";

      const url = URL.createObjectURL(respuesta.data as Blob);
      const enlace = document.createElement("a");
      enlace.href = url;
      enlace.download = nombre;
      enlace.click();
      URL.revokeObjectURL(url);
    },
  });
}
