"""Los workers deben abrir y cerrar asyncpg dentro del mismo event loop."""

import asyncio
from types import SimpleNamespace
from uuid import uuid4

from app.workers.tasks import documents, exports, outbox


class MotorFalso:
    def __init__(self, eventos: list[tuple[str, object]]) -> None:
        self.eventos = eventos

    async def dispose(self) -> None:
        self.eventos.append(("dispose", asyncio.get_running_loop()))


class SesionFalsa:
    async def __aenter__(self) -> "SesionFalsa":
        return self

    async def __aexit__(self, *_: object) -> None:
        return None

    async def commit(self) -> None:
        return None


def fabrica_sesion() -> SesionFalsa:
    return SesionFalsa()


def test_documentos_cierra_el_pool_en_el_loop_de_la_tarea(monkeypatch) -> None:
    eventos: list[tuple[str, object]] = []
    motor = MotorFalso(eventos)

    async def procesar(_session, *, document_id):
        eventos.append(("trabajo", asyncio.get_running_loop()))
        return SimpleNamespace(
            document_id=document_id,
            upload_status="READY",
            size_bytes=123,
        )

    monkeypatch.setattr(documents, "get_sessionmaker", lambda: fabrica_sesion)
    monkeypatch.setattr(documents, "get_engine", lambda: motor)
    monkeypatch.setattr(documents, "procesar_documento", procesar)

    resultado = documents.tarea_procesar_documento.run(str(uuid4()))

    assert resultado["status"] == "READY"
    assert eventos[0][1] is eventos[1][1]


def test_exportaciones_cierra_el_pool_en_el_loop_de_la_tarea(monkeypatch) -> None:
    eventos: list[tuple[str, object]] = []
    motor = MotorFalso(eventos)

    async def procesar(job_id):
        eventos.append(("trabajo", asyncio.get_running_loop()))
        return {"job_id": str(job_id), "status": "READY"}

    monkeypatch.setattr(exports, "_procesar", procesar)
    monkeypatch.setattr(exports, "get_engine", lambda: motor)

    resultado = exports.tarea_exportar_documentos.run(str(uuid4()))

    assert resultado["status"] == "READY"
    assert eventos[0][1] is eventos[1][1]


def test_outbox_cierra_el_pool_en_el_loop_de_la_tarea(monkeypatch) -> None:
    eventos: list[tuple[str, object]] = []
    motor = MotorFalso(eventos)

    async def procesar(_session, _manejador, *, limite):
        assert limite == outbox.LOTE
        eventos.append(("trabajo", asyncio.get_running_loop()))
        return SimpleNamespace(entregados=2, reintentar=1, agotados=0)

    monkeypatch.setattr(outbox, "get_sessionmaker", lambda: fabrica_sesion)
    monkeypatch.setattr(outbox, "get_engine", lambda: motor)
    monkeypatch.setattr(outbox, "procesar_lote", procesar)

    resultado = outbox.tarea_procesar_pendientes.run()

    assert resultado == {"entregados": 2, "reintentar": 1, "agotados": 0}
    assert eventos[0][1] is eventos[1][1]
