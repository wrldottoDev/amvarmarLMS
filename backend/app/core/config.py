from functools import lru_cache

from pydantic import model_validator
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
    # NO se envía a `gpt-5.6-luna`: es un modelo de razonamiento y la API
    # rechaza `temperature` con 400 (verificado, ADR-0012 enmienda 2026-09).
    # Se conserva el campo por si el modelo configurado cambia a uno que sí lo
    # admita; `provider.py` decide en runtime cuál de los dos enviar.
    copilot_temperature: float = 0.2
    # Control de determinismo real para modelos de razonamiento como el
    # configurado hoy. "low" mantiene la intención original: para datos
    # logísticos, una respuesta creativa es una respuesta equivocada.
    copilot_reasoning_effort: str = "low"
    # Sin default y opcional: el módulo no está activo todavía. Cuando lo esté,
    # la ausencia de clave debe impedir que el endpoint funcione, no que la app
    # entera no arranque.
    openai_api_key: str | None = None
    copilot_timeout_segundos: float = 30.0
    # Circuit breaker en memoria de proceso: tras N fallos seguidos del
    # proveedor, deja de intentar por un rato en vez de que cada request espere
    # el timeout completo. La caída del proveedor no puede parecer una caída
    # del LMS.
    copilot_breaker_fallos_para_abrir: int = 5
    copilot_breaker_segundos_abierto: float = 60.0
    copilot_max_mensajes_por_turno: int = 20
    copilot_max_tool_calls_por_turno: int = 5
    copilot_max_tokens_salida: int = 1000
    # ADR-0012: clientes tienen tope mensual; personal interno no.
    copilot_limite_mensajes_cliente_por_mes: int = 100
    # Fase 4: cuánto dura una propuesta de escritura (`PropuestaAccion`)
    # pendiente de confirmación antes de vencer. Ni tan corto que la persona
    # no llegue a revisarla, ni tan largo que confirme algo desactualizado.
    copilot_propuesta_ttl_minutos: int = 30
    # Solo para Playwright (ADR-0012, Fase 3): cambia `ProveedorOpenAI` por
    # `ProveedorFalsoDeterministico` en `_fabrica_proveedor` (copilot/router.py),
    # así el e2e prueba el turno completo (frontend → backend → ejecutor) sin
    # tocar la API real ni necesitar `OPENAI_API_KEY`. El validador de abajo
    # impide que esto llegue a producción por una variable olvidada.
    copilot_proveedor_falso: bool = False

    @model_validator(mode="after")
    def _proveedor_falso_solo_en_local(self) -> "Settings":
        if self.copilot_proveedor_falso and self.environment != "local":
            raise ValueError("COPILOT_PROVEEDOR_FALSO solo puede activarse con ENVIRONMENT=local.")
        return self

    @property
    def copilot_habilitado(self) -> bool:
        return bool(self.openai_api_key) or self.copilot_proveedor_falso

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
