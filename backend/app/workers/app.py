"""Aplicación Celery.

Redis como broker: ya está en la infraestructura y el volumen de tareas de este
sistema (correos, archivado) no justifica sumar RabbitMQ.
"""

from celery import Celery
from celery.schedules import crontab

from app.core.config import get_settings


def crear_celery() -> Celery:
    settings = get_settings()

    celery = Celery(
        "amvarmar",
        broker=settings.redis_url,
        backend=settings.redis_url,
        include=[
            "app.workers.tasks.outbox",
            "app.workers.tasks.documents",
            "app.workers.tasks.exports",
            "app.workers.tasks.copilot",
            "app.workers.tasks.shipments",
            "app.workers.tasks.archivado",
        ],
    )

    celery.conf.update(
        task_serializer="json",
        result_serializer="json",
        accept_content=["json"],
        timezone="UTC",
        enable_utc=True,
        # El worker confirma la tarea DESPUÉS de ejecutarla: si el proceso muere
        # a mitad de una tarea, esta vuelve a la cola en vez de perderse.
        task_acks_late=True,
        # Sin prefetch: una tarea a la vez por worker. El trabajo es intensivo en
        # memoria y acumular varias en el buffer local las dejaría bloqueadas si
        # el worker muere.
        worker_prefetch_multiplier=1,
        # Una tarea que tarda más que esto es un problema, no una espera. Tres
        # minutos: el lote más pesado es el del outbox, que hace red por evento.
        task_time_limit=180,
        task_annotations={
            # Un expediente grande puede superar ampliamente tres minutos. El
            # proceso usa disco temporal y memoria acotada, por lo que darle
            # una hora no reserva RAM proporcional al tamaño del ZIP.
            "documents.export": {
                "soft_time_limit": 3300,
                "time_limit": 3600,
            },
            # Ghostscript sobre un lote de PDFs puede superar los 3 minutos
            # globales sin ser un problema real — es CPU, no un cuelgue.
            "documents.recompress_archived": {
                "soft_time_limit": 900,
                "time_limit": 1200,
            },
        },
        beat_schedule={
            "expire-document-exports-hourly": {
                "task": "documents.expire_exports",
                "schedule": 3600.0,
            },
            # Cada 10 minutos, no cada hora: el TTL de una propuesta
            # (`copilot_propuesta_ttl_minutos`) es 30 minutos por defecto —
            # con un barrido horario, una propuesta vencida podría figurar
            # `PENDING` casi una hora de más.
            "expire-copilot-proposals": {
                "task": "copilot.expire_proposals",
                "schedule": 600.0,
            },
            # ADR-0008: aviso de "documentos por archivar", una semana antes
            # de que `archive-pending-shipments-daily` los archive de verdad.
            "notify-archiving-soon-daily": {
                "task": "shipments.notify_archiving_soon",
                "schedule": crontab(hour=2, minute=45),
            },
            # ADR-0007: "un barrido periódico (ej. diario)" — a las 3am, fuera
            # de horario de oficina, distinto del patrón reactivo del outbox.
            "archive-pending-shipments-daily": {
                "task": "shipments.archive_pending",
                "schedule": crontab(hour=3, minute=0),
            },
            # 30 minutos después: recomprimir sobre cargas YA marcadas
            # archivadas, no antes — un fallo acá nunca debe impedir que el
            # barrido de arriba archive a tiempo.
            "recompress-archived-documents-daily": {
                "task": "documents.recompress_archived",
                "schedule": crontab(hour=3, minute=30),
            },
        },
    )

    return celery


celery_app = crear_celery()
