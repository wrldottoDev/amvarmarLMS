"""Aplicación Celery.

Redis como broker: ya está en la infraestructura y el volumen de tareas de este
sistema (correos, archivado) no justifica sumar RabbitMQ.
"""

from celery import Celery

from app.core.config import get_settings


def crear_celery() -> Celery:
    settings = get_settings()

    celery = Celery(
        "amvarmar",
        broker=settings.redis_url,
        backend=settings.redis_url,
        include=["app.workers.tasks.outbox"],
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
    )

    return celery


celery_app = crear_celery()
