#!/usr/bin/env bash
# Réplica local de .github/workflows/ci.yml: mismos jobs, mismos comandos.
# Si cambia el workflow, este script cambia con él.
#
# Uso:   scripts/ci_local.sh            # todos los jobs
#        scripts/ci_local.sh calidad    # uno o varios por nombre
#
# Requiere: backend/.venv con requirements-dev.txt, Node 22, Docker.
set -uo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
export PATH="$REPO/backend/.venv/bin:$PATH"

# Los jobs corren sobre una copia con lo que Git versionaría (tracked + nuevos no
# ignorados), igual que el checkout de CI. Así no se cuelan `backend/.env`,
# `frontend/.env.local` ni node_modules locales: `Settings` lee `.env` y, sin
# esta copia, ENVIRONMENT=local y el Redis de desarrollo tapaban fallos reales.
RAIZ="$(mktemp -d)"
trap 'rm -rf "$RAIZ"' EXIT
python - "$REPO" "$RAIZ" <<'FIN'
import os, shutil, subprocess, sys

repo, copia = sys.argv[1:]
salida = subprocess.run(
    ["git", "-C", repo, "ls-files", "-z", "-co", "--exclude-standard"],
    capture_output=True, check=True,
).stdout.decode()
for ruta in filter(None, salida.split("\0")):
    origen = os.path.join(repo, ruta)
    if os.path.isfile(origen):  # los borrados sin commitear siguen listados
        destino = os.path.join(copia, ruta)
        os.makedirs(os.path.dirname(destino), exist_ok=True)
        shutil.copy2(origen, destino)
FIN
BACKEND="$RAIZ/backend"
FRONTEND="$RAIZ/frontend"

# Mismas variables de juguete que el job `pruebas` del workflow.
ENV_CI=(
  ENVIRONMENT=ci
  DEBUG=false
  REDIS_URL=redis://localhost:6379/0
  JWT_SIGNING_KEY=clave-de-firma-solo-para-ci-no-usar-en-produccion
  REFRESH_FINGERPRINT_KEY=clave-de-fingerprint-solo-para-ci-no-usar
  FCM_TOKEN_ENCRYPTION_KEY=clave-de-cifrado-solo-para-ci-no-usar
)

calidad() {
  cd "$BACKEND" &&
    ruff check app tests scripts alembic &&
    ruff format --check app tests scripts alembic &&
    mypy app scripts
}

pruebas() {
  # Valor de juguete, igual que en el workflow: los tests usan testcontainers.
  local url="postgresql+asyncpg://ci:ci@localhost:5432/ci"  # pragma: allowlist secret
  cd "$BACKEND" &&
    env "${ENV_CI[@]}" DATABASE_URL="$url" \
      pytest -q -m "not slow" --cov=app --cov-report=term-missing
}

frontend() {
  cd "$FRONTEND" &&
    npm ci &&
    npm run lint &&
    npm run types &&
    npm test &&
    npm run build
}

migraciones() {
  # El 5432 local suele estar ocupado por el Postgres de desarrollo: se usa otro
  # puerto con un contenedor desechable, igual al `services:` del workflow.
  local nombre=amvarmar-ci-migraciones puerto=55432
  local url="postgresql+asyncpg://amvarmar:ci-solo-para-pruebas@localhost:$puerto/amvarmar_lms"  # pragma: allowlist secret
  docker rm -f "$nombre" >/dev/null 2>&1
  docker run -d --name "$nombre" -p "$puerto:5432" \
    -e POSTGRES_DB=amvarmar_lms -e POSTGRES_USER=amvarmar \
    -e POSTGRES_PASSWORD=ci-solo-para-pruebas postgres:16-alpine >/dev/null || return 1
  until docker exec "$nombre" pg_isready -U amvarmar -d amvarmar_lms >/dev/null 2>&1; do sleep 1; done
  # pg_isready responde antes de que termine el initdb del entrypoint.
  sleep 2

  (
    cd "$BACKEND" &&
      export "${ENV_CI[@]}" \
        DATABASE_URL="$url" &&
      alembic upgrade head &&
      alembic downgrade base &&
      alembic upgrade head &&
      alembic check &&
      python -m scripts.seed_rbac &&
      python -m scripts.seed_rbac
  )
  local rc=$?
  docker rm -f "$nombre" >/dev/null
  return $rc
}

seguridad() {
  # detect-secrets escanea lo versionado en Git: necesita el repo real, no la copia.
  cd "$REPO" || return 1
  local antes
  antes="$(mktemp)"
  cp .secrets.baseline "$antes"
  detect-secrets scan \
    --baseline .secrets.baseline \
    --exclude-files 'backend/\.venv/.*|frontend/node_modules/.*' \
    --exclude-lines '^(revision|down_revision|Revision ID|Revises)' &&
    python - "$antes" <<'FIN'
import json, sys

antes = json.load(open(sys.argv[1]))["results"]
despues = json.load(open(".secrets.baseline"))["results"]

if antes != despues:
    nuevos = {k: v for k, v in despues.items() if antes.get(k) != v}
    print("Secretos nuevos sin auditar:", json.dumps(nuevos, indent=2))
    print("Revise cada uno y, si son valores de prueba, actualice .secrets.baseline")
    sys.exit(1)
print("Sin secretos nuevos.")
FIN
  local rc=$?
  # En CI el runner descarta el checkout; acá el escaneo reescribe
  # `generated_at` y ensuciaría el árbol. Si hubo hallazgos queda el archivo
  # nuevo para revisarlo.
  [ $rc -eq 0 ] && cp "$antes" .secrets.baseline
  rm -f "$antes"
  [ $rc -eq 0 ] || return $rc

  cd "$REPO/backend" &&
    pip-audit --requirement requirements.txt --strict &&
    bandit -c pyproject.toml -r app scripts
}

imagen() {
  local tag=amvarmar-lms-backend:ci
  docker build -t "$tag" "$BACKEND" || return 1
  if docker run --rm --entrypoint sh "$tag" -c 'ls /app/.env' 2>/dev/null; then
    echo "La imagen contiene un archivo .env"
    return 1
  fi
  echo "Sin .env en la imagen"
  # Misma versión de trivy que trae aquasecurity/trivy-action@0.28.0.
  docker run --rm -v /var/run/docker.sock:/var/run/docker.sock \
    aquasec/trivy:0.56.1 image --format table --exit-code 1 \
    --severity CRITICAL,HIGH --ignore-unfixed "$tag"
}

JOBS=("$@")
[ ${#JOBS[@]} -eq 0 ] && JOBS=(calidad pruebas frontend migraciones seguridad imagen)

declare -a RESUMEN
FALLO=0
for job in "${JOBS[@]}"; do
  echo "::::: $job :::::"
  if ( "$job" ); then
    RESUMEN+=("PASS  $job")
  else
    RESUMEN+=("FAIL  $job")
    FALLO=1
  fi
done

echo
printf '%s\n' "${RESUMEN[@]}"
exit $FALLO
