# Multi-etapa: las herramientas de compilación (gcc, headers) quedan fuera de la
# imagen final. Menos superficie y menos CVEs que reportar.
FROM python:3.12-slim AS builder

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt ./
RUN pip install --prefix=/instalacion -r requirements.txt


FROM python:3.12-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# libmagic es requisito de python-magic (validación de MIME real, Paso 3.1).
RUN apt-get update \
    && apt-get install -y --no-install-recommends libmagic1 \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 10001 amvarmar

COPY --from=builder /instalacion /usr/local

WORKDIR /app

# Copia explícita, no `COPY . .`: así un `.env` presente en el contexto de build
# no puede colarse en la imagen por descuido. El .dockerignore es la segunda
# barrera, no la única.
COPY --chown=amvarmar:amvarmar app ./app
COPY --chown=amvarmar:amvarmar alembic ./alembic
COPY --chown=amvarmar:amvarmar scripts ./scripts
COPY --chown=amvarmar:amvarmar alembic.ini pyproject.toml ./

# Sin root: si alguien logra ejecución dentro del contenedor, no arranca con
# privilegios.
USER amvarmar

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request;urllib.request.urlopen('http://localhost:8000/health/live')"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
