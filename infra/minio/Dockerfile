# MinIO compilado desde el código fuente, en una versión fija.
#
# MinIO dejó de publicar imágenes de la edición comunitaria (minio/minio,
# quay.io/minio/minio y los tags RELEASE.* ya no se pueden descargar) y archivó
# su repositorio. Esta es la última versión comunitaria, la misma que usan el
# desarrollo y los tests. NO recibe más parches de seguridad: hay que migrar a
# un storage S3 mantenido antes del cutover (ver docs/runbooks/despliegue-piloto.md).
# Licencia AGPL-3.0: uso interno, sin modificar el código.
ARG MINIO_RELEASE=RELEASE.2025-10-15T17-29-55Z

FROM golang:1.24-alpine AS build
ARG MINIO_RELEASE
RUN apk add --no-cache git
RUN git clone --depth 1 --branch "${MINIO_RELEASE}" https://github.com/minio/minio.git /src
WORKDIR /src
# Las mismas banderas que el Makefile oficial (`make build`), sin sus checks.
RUN CGO_ENABLED=0 go build -tags kqueue -trimpath \
      -ldflags "$(go run buildscripts/gen-ldflags.go)" -o /minio .

FROM alpine:3.22
RUN apk add --no-cache ca-certificates \
    && adduser -D -u 10001 minio \
    && mkdir /data && chown minio:minio /data
COPY --from=build /minio /usr/local/bin/minio
USER minio
EXPOSE 9000 9001
VOLUME ["/data"]
HEALTHCHECK --interval=10s --timeout=3s --retries=10 \
  CMD wget -q -O /dev/null http://127.0.0.1:9000/minio/health/live || exit 1
ENTRYPOINT ["minio"]
CMD ["server", "/data", "--console-address", ":9001"]
