from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Configuración validada de la aplicación.

    Todo campo sin default es obligatorio: la app no arranca si falta la variable
    de entorno correspondiente (falla rápido, no en silencio en el primer request).
    """

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    environment: str
    debug: bool = False

    database_url: str
    redis_url: str

    # Tres claves separadas (Paso 1.6): firma JWT, fingerprint de refresh, cifrado
    # de tokens FCM. Nunca la misma clave para dos propósitos distintos.
    jwt_signing_key: str
    refresh_fingerprint_key: str
    fcm_token_encryption_key: str

    # `iss` y `aud` se validan en cada token, no solo la firma: un token válido
    # emitido para otro entorno (staging) no debe servir en este.
    jwt_issuer: str = "amvarmar-lms"
    jwt_audience: str = "amvarmar-lms-api"

    # Access corto a propósito: es el que viaja en cada request y no se puede
    # revocar antes de que expire. La revocación real vive en la sesión.
    access_token_ttl_seconds: int = 600  # 10 minutos

    # Ventana de inactividad del refresh. Cada rotación la renueva.
    refresh_token_ttl_seconds: int = 60 * 60 * 24 * 14  # 14 días

    # Tope duro de la sesión: no se extiende por más rotaciones que haya.
    # Obliga a autenticarse de nuevo aunque el usuario esté siempre activo.
    session_absolute_ttl_seconds: int = 60 * 60 * 24 * 30  # 30 días

    # Margen de tolerancia para desfase de reloj entre servidores al validar
    # `exp` y `nbf`.
    jwt_leeway_seconds: int = 10

    # --- Storage de documentos (Paso 3.1) ---
    # MinIO en local, S3 o equivalente en producción. El endpoint es
    # configurable para no atar el código a un proveedor.
    s3_endpoint_url: str = "http://localhost:9000"
    s3_access_key: str = "amvarmar"
    s3_secret_key: str = ""
    s3_region: str = "us-east-1"
    s3_bucket: str = "amvarmar-documentos"
    document_export_ttl_hours: int = 24
    document_export_temp_dir: str | None = None

    # --- Antivirus (Paso 3.2) ---
    # Un archivo de 250 MB tarda; el timeout es por operación, no por byte.

    # --- Asistente virtual (ADR-0012) ---
    # El identificador del modelo NUNCA se escribe en el código: los proveedores
    # renombran y retiran modelos, y un ID hardcodeado obliga a desplegar para
    # cambiarlo.
    copilot_name: str = "AMVI"
    copilot_model: str = "gpt-5.6-luna"
    # Temperatura baja: en un contexto logístico, una respuesta creativa sobre
    # dónde está una carga es una respuesta equivocada.
    copilot_temperature: float = 0.2
    # Sin default y opcional: el módulo no está activo todavía. Cuando lo esté,
    # la ausencia de clave debe impedir que el endpoint funcione, no que la app
    # entera no arranque.
    openai_api_key: str | None = None

    @property
    def copilot_habilitado(self) -> bool:
        return bool(self.openai_api_key)

    # --- Correo (Paso 4.2) ---
    # Mailpit en local y en las pruebas; un relay real en producción. El correo
    # NUNCA lleva datos de negocio ni adjuntos, solo contexto mínimo y un enlace
    # para entrar al sistema (ADR-0008).
    smtp_host: str = "localhost"
    smtp_port: int = 1025
    smtp_username: str | None = None
    smtp_password: str | None = None
    # Mailpit no habla TLS. En producción es obligatorio: sin esto las
    # credenciales del relay viajarían en claro.
    smtp_use_tls: bool = False
    smtp_timeout_seconds: int = 20

    email_from: str = "AMVARMAR <no-responder@amvarmar.com>"
    # Base de los enlaces del correo. Si apuntara al backend, el usuario
    # aterrizaría en un JSON en vez de en la pantalla que le corresponde.
    frontend_base_url: str = "http://localhost:3000"

    # --- Observabilidad (Paso 4.3) ---
    # `/metrics` NO es público: expone rutas internas, volumen de usuarios y
    # patrones de fallo de login, que es reconocimiento gratis para un atacante.
    # Sin token configurado el endpoint queda deshabilitado fuera de local —
    # fail closed, para que un despliegue que olvida la variable no lo publique.
    metrics_token: str | None = None

    # Endpoint OTLP del colector de trazas. Sin esto no se instrumenta nada:
    # exportar a un destino que no existe llena los logs de errores de conexión.
    otel_exporter_endpoint: str | None = None
    otel_service_name: str = "amvarmar-lms-api"

    @property
    def metricas_habilitadas(self) -> bool:
        return bool(self.metrics_token) or self.environment == "local"

    @property
    def cookie_secure(self) -> bool:
        # En local se sirve por http; forzar Secure impediría probar el login.
        return self.environment != "local"


@lru_cache
def get_settings() -> Settings:
    return Settings()
