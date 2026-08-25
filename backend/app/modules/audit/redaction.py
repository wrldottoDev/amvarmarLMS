"""Redacción de datos sensibles antes de escribir en auditoría.

La bitácora se conserva 2 años (ADR-0007) y la consultan administradores y
clientes. Un secreto que entre aquí queda expuesto durante todo ese tiempo, en
un lugar donde nadie lo busca.

La lista es de bloqueo, no de permiso: se redacta lo que coincide y pasa el
resto. Es la decisión pragmática — enumerar todo lo permitido haría que
cualquier campo nuevo de negocio se perdiera del log por olvido. A cambio,
`_FRAGMENTOS_PROHIBIDOS` está deliberadamente amplia: atrapa `password`,
`user_password`, `passwordHash` y `nueva_password` con una sola entrada.
"""

from typing import Any

REDACTADO = "«redactado»"

# Coincidencia por SUBCADENA sobre el nombre de la clave, en minúsculas.
# Cubre variantes en inglés y español porque los payloads mezclan ambos.
_FRAGMENTOS_PROHIBIDOS: frozenset[str] = frozenset(
    {
        # Contraseñas y hashes
        "password",
        "contrasena",
        "contraseña",
        "passwd",
        "password_hash",
        "hash",
        # Tokens y sesiones
        "token",
        "jwt",
        "access_token",
        "refresh",
        "cookie",
        "session_secret",
        "authorization",
        "bearer",
        # Claves y credenciales
        "secret",
        "api_key",
        "apikey",
        "private_key",
        "credential",
        "credencial",
        "fingerprint",
        "signature",
        "firma",
        # Contenido de archivo: nunca va a auditoría, solo su metadata
        "file_content",
        "contenido_archivo",
        "raw_body",
        "payload_completo",
        # Datos de pago
        "card_number",
        "cvv",
        "tarjeta",
    }
)

# Profundidad máxima al recorrer estructuras anidadas. Un payload
# malintencionadamente profundo no debe agotar la pila al auditarse.
_PROFUNDIDAD_MAXIMA = 12


def _clave_es_sensible(clave: str) -> bool:
    minuscula = clave.lower()
    return any(fragmento in minuscula for fragmento in _FRAGMENTOS_PROHIBIDOS)


def redactar(valor: Any, *, _profundidad: int = 0) -> Any:
    """Devuelve una copia con los valores sensibles reemplazados.

    No modifica la entrada: el objeto original sigue siendo utilizable por el
    código de negocio que lo pasó.
    """
    if _profundidad >= _PROFUNDIDAD_MAXIMA:
        return REDACTADO

    if isinstance(valor, dict):
        return {
            clave: (
                REDACTADO
                if _clave_es_sensible(str(clave))
                else redactar(contenido, _profundidad=_profundidad + 1)
            )
            for clave, contenido in valor.items()
        }

    if isinstance(valor, list | tuple):
        return [redactar(elemento, _profundidad=_profundidad + 1) for elemento in valor]

    return valor
