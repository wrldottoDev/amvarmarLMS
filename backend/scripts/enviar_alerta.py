"""Manda una alerta de operación por correo, con el mismo SMTP que los avisos.

Lo usa `infra/backup/vigilar.sh` (disco lleno, respaldos que no corrieron).
Va directo al relay y no por el outbox: si la base o el worker están caídos,
la alerta igual tiene que salir.

    python -m scripts.enviar_alerta --para ops@example.com --asunto "..." --texto "..."
"""

import argparse
import asyncio
import html

from app.modules.notifications.email import CorreoCompuesto, enviar


def componer(asunto: str, texto: str) -> CorreoCompuesto:
    return CorreoCompuesto(
        asunto=f"[AMVARMAR LMS] {asunto}",
        texto=texto,
        # El texto es de la VPS (salida de `df`, rutas): se escapa igual.
        html=f"<pre style='font-family:monospace'>{html.escape(texto)}</pre>",
        enlace="",
    )


def principal() -> None:
    parser = argparse.ArgumentParser(description="Manda una alerta de operación por correo.")
    parser.add_argument("--para", required=True, help="Destinatario.")
    parser.add_argument("--asunto", required=True)
    parser.add_argument("--texto", required=True)
    argumentos = parser.parse_args()

    asyncio.run(enviar(argumentos.para, componer(argumentos.asunto, argumentos.texto)))
    print(f"Alerta enviada a {argumentos.para}.")


if __name__ == "__main__":
    principal()
