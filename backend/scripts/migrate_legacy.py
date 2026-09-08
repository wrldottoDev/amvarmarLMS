"""Migrador del sistema viejo al nuevo (Paso 5.2).

Repetible e idempotente. `legacy_id_map` guarda a qué registro nuevo corresponde
cada registro viejo, así que una segunda corrida reconoce lo ya migrado en vez
de duplicarlo.

    python -m scripts.migrate_legacy --dry-run     # no escribe nada
    python -m scripts.migrate_legacy               # migra

La base vieja se lee con `psycopg` en modo síncrono y la nueva se escribe con
SQLAlchemy. Son dos conexiones distintas a propósito: leer y escribir en la
misma transacción ataría el ritmo de la migración al de la lectura.

Lo que este script NO hace:

- **No arregla datos rotos.** Si un usuario tiene el correo vacío o duplicado,
  aborta y dice cuál. Esas correcciones son el Paso 5.1 y van en el sistema
  viejo, con traza. Un migrador lleno de excepciones para datos malos es un
  migrador que nadie puede volver a correr.
- **No sube archivos.** Los documentos se registran con su ruta del legacy y
  quedan en `UPLOADING`; subirlos al storage privado es el Paso 5.3.
- **No inventa estados.** Todo caso que ADR-0002 no resuelve con datos queda con
  `legacy_review_required = true`.
"""

import argparse
import asyncio
import os
import re
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID, uuid4

import psycopg
from psycopg.rows import dict_row
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_engine, get_sessionmaker
from app.modules.documents.validation import nombre_seguro
from app.modules.rbac.models import RoleCode, ScopeType
from app.modules.shipments.models import EventType, ReferenceType, ShipmentStatus

# Conexión a la base vieja. Se define acá para que los demás scripts de la fase
# usen la misma y no haya dos cadenas que puedan divergir.
LEGACY_URL_POR_DEFECTO = os.environ.get(
    "LEGACY_DATABASE_URL", "postgresql://amvarmar@localhost:5435/amvarmar_legacy"
)

# --- Traducción de estados (ADR-0002) ---

# `COMPLETADO` no pasa a DELIVERED: el legacy no guarda prueba de entrega, y
# afirmar una entrega sin evidencia es peor que dejarla en despachada.
_ESTADO_DIRECTO = {
    "COMPLETADO": ShipmentStatus.DISPATCHED,
}

# Estados en los que todavía tiene sentido pedir documentos. Después de
# despachar, un requisito abierto sería trabajo pendiente sobre algo que ya pasó.
_ESTADOS_QUE_AUN_EXIGEN_DOCUMENTOS = frozenset(
    {
        ShipmentStatus.PRE_ALERT,
        ShipmentStatus.IN_TRANSIT,
        ShipmentStatus.RECEIVED,
        ShipmentStatus.STORED,
        ShipmentStatus.DISPATCH_REQUESTED,
        ShipmentStatus.PREPARING,
    }
)

# Métodos de despacho del legacy.
_METODO = {
    "MARITIMO": "SEA",
    "MARÍTIMO": "SEA",
    "AEREO": "AIR",
    "AÉREO": "AIR",
    "TERRESTRE": "LAND",
}

# Tipos de bulto: el legacy los guarda en español y en plural.
_TIPO_BULTO = {
    "PALLETS": "PALLET",
    "CAJAS": "BOX",
    "TAMBORES": "DRUM",
    "ATADOS": "BUNDLE",
    "BULTOS": "BUNDLE",
    "OTRO": "OTHER",
}

# Estados de solicitud de despacho del legacy.
_ESTADO_DESPACHO = {
    "PENDIENTE": "PENDING",
    "APROBADO": "APPROVED",
    "COMPLETADO": "COMPLETED",
    "RECHAZADO": "REJECTED",
}


@dataclass
class Conteo:
    leidos: int = 0
    creados: int = 0
    ya_existian: int = 0
    marcados_revision: int = 0


@dataclass
class Reporte:
    tablas: dict[str, Conteo] = field(default_factory=dict)
    # Impiden migrar: el script termina con error y no se debe seguir.
    problemas: list[str] = field(default_factory=list)
    # Cosas que hay que saber pero que ya están resueltas de una forma decidida
    # —por ejemplo, cargas sin cliente que se marcan para revisión—. Mezclarlas
    # con los problemas haría que el cutover abortara por una condición
    # esperada, y a la tercera vez nadie leería la diferencia.
    avisos: list[str] = field(default_factory=list)

    def contador(self, tabla: str) -> Conteo:
        return self.tablas.setdefault(tabla, Conteo())

    def imprimir(self, *, seco: bool) -> None:
        titulo = "SIMULACIÓN (no se escribió nada)" if seco else "MIGRACIÓN COMPLETADA"
        print(f"\n{'=' * 66}\n{titulo}\n{'=' * 66}")
        print(f"{'tabla destino':<28}{'leídos':>8}{'creados':>9}{'ya estaban':>12}{'revisión':>10}")
        print("-" * 66)
        for tabla, c in self.tablas.items():
            print(
                f"{tabla:<28}{c.leidos:>8}{c.creados:>9}{c.ya_existian:>12}"
                f"{c.marcados_revision:>10}"
            )

        for titulo_lista, elementos in (
            ("problema(s) que impiden migrar", self.problemas),
            ("aviso(s)", self.avisos),
        ):
            if not elementos:
                continue
            print(f"\n{len(elementos)} {titulo_lista}:")
            for linea in elementos[:40]:
                print(f"  - {linea}")
            if len(elementos) > 40:
                print(f"  ... y {len(elementos) - 40} más")


def normalizar_wr(valor: str) -> str:
    """Quita los caracteres de captura del WR.

    Se hace acá y NO en la base vieja: allí el WR es la clave primaria y las
    cinco claves foráneas que lo referencian son NO ACTION, así que cambiarlo
    fallaría en cuanto la carga tenga un documento o una pieza. En el esquema
    nuevo el WR es una referencia más (ADR-0005), y ahí normalizarlo es
    inofensivo.
    """
    return re.sub(r"[^A-Za-z0-9-]", "", valor).upper()


class Migrador:
    def __init__(self, legacy: psycopg.Connection, session: AsyncSession, *, seco: bool):
        self.legacy = legacy
        self.session = session
        self.seco = seco
        self.reporte = Reporte()
        # Cache de correspondencias, para no consultar `legacy_id_map` por fila.
        self._mapa: dict[tuple[str, str], UUID] = {}

    # --- Correspondencias ---

    async def _cargar_mapa(self) -> None:
        filas = (
            await self.session.execute(
                text("SELECT legacy_table, legacy_pk, new_uuid FROM legacy_id_map")
            )
        ).all()
        self._mapa = {(f.legacy_table, f.legacy_pk): f.new_uuid for f in filas}

    def buscar(self, tabla: str, pk: Any) -> UUID | None:
        return self._mapa.get((tabla, str(pk)))

    async def _registrar(
        self, tabla_legacy: str, pk: Any, tabla_nueva: str, nuevo_id: UUID, nota: str | None = None
    ) -> None:
        self._mapa[(tabla_legacy, str(pk))] = nuevo_id
        if self.seco:
            return
        await self.session.execute(
            text("""
                INSERT INTO legacy_id_map (legacy_table, legacy_pk, new_table, new_uuid, nota)
                VALUES (:t, :pk, :nt, :id, :nota)
                ON CONFLICT (legacy_table, legacy_pk) DO NOTHING
            """),
            {"t": tabla_legacy, "pk": str(pk), "nt": tabla_nueva, "id": nuevo_id, "nota": nota},
        )

    def _leer(self, consulta: str) -> list[dict[str, Any]]:
        with self.legacy.cursor(row_factory=dict_row) as cur:
            cur.execute(consulta)
            return list(cur.fetchall())

    # --- Comprobaciones previas ---

    async def comprobar(self) -> bool:
        """Verifica que los datos se puedan migrar. No arregla nada.

        Si esto falla, el arreglo va en el sistema viejo (Paso 5.1), no acá.
        """
        vacios = self._leer("""
            SELECT id, username FROM auth_user
            WHERE coalesce(trim(email), '') = '' AND is_active
        """)
        for u in vacios:
            self.reporte.problemas.append(
                f"auth_user {u['id']} ({u['username']}): correo vacío. Paso 5.1."
            )

        duplicados = self._leer("""
            SELECT lower(trim(email)) AS email, string_agg(id::text, ', ') AS ids
            FROM auth_user
            WHERE coalesce(trim(email), '') <> '' AND is_active
            GROUP BY 1 HAVING count(*) > 1
        """)
        for d in duplicados:
            self.reporte.problemas.append(
                f"correo duplicado {d['email']} en usuarios {d['ids']}. Paso 5.1."
            )

        # El migrador asigna roles, pero los roles sin permisos no sirven de
        # nada: el usuario entra y no ve una sola carga. Pasó de verdad al
        # migrar contra una base cuyo `role_permissions` estaba vacío, y el
        # síntoma —pantallas en blanco— no apunta a la causa.
        faltantes = (
            await self.session.execute(
                text("""
                    SELECT count(*) FROM roles r
                    WHERE NOT EXISTS (
                        SELECT 1 FROM role_permissions rp WHERE rp.role_id = r.id
                    )
                """)
            )
        ).scalar_one()
        if faltantes:
            self.reporte.problemas.append(
                f"{faltantes} rol(es) sin permisos asignados. "
                "Corra `python -m scripts.seed_rbac` antes de migrar."
            )
            return False

        tipos = {
            str(f.code).upper(): str(f.context)
            for f in (
                await self.session.execute(
                    text("SELECT code, context FROM document_types WHERE is_active")
                )
            ).all()
        }
        esperados = {
            "COMMERCIAL_INVOICE": "SHIPMENT",
            "BL": "DISPATCH",
            "WAREHOUSE_RECEIPT": "SHIPMENT",
            "LEGACY_UNCLASSIFIED": "SHIPMENT",
        }
        tipos_invalidos = [
            f"{codigo} ({tipos.get(codigo, 'faltante')} != {contexto})"
            for codigo, contexto in esperados.items()
            if tipos.get(codigo) != contexto
        ]
        if tipos_invalidos:
            self.reporte.problemas.append(
                "Catálogo documental incompleto o con contexto incorrecto: "
                + ", ".join(tipos_invalidos)
                + ". Corra `python -m scripts.seed_document_types`."
            )

        sin_piezas = self._leer("""
            SELECT w.wr_number
            FROM core_warehouse w
            WHERE NOT EXISTS (
                SELECT 1 FROM core_piecewarehouse p WHERE p.warehouse_id = w.wr_number
            )
            ORDER BY w.wr_number
        """)
        if sin_piezas:
            muestra = ", ".join(str(f["wr_number"]) for f in sin_piezas[:10])
            self.reporte.problemas.append(
                f"{len(sin_piezas)} carga(s) legacy no tienen piezas ({muestra}). "
                "Deben corregirse antes de migrar; el LMS no permite inventarlas."
            )

        wr_por_normalizado: dict[str, list[str]] = {}
        for fila in self._leer("SELECT wr_number FROM core_warehouse ORDER BY wr_number"):
            wr = str(fila["wr_number"])
            wr_por_normalizado.setdefault(normalizar_wr(wr), []).append(wr)
        colisiones = [valores for valores in wr_por_normalizado.values() if len(valores) > 1]
        if colisiones:
            muestras = "; ".join(" / ".join(grupo) for grupo in colisiones[:10])
            self.reporte.problemas.append(
                f"{len(colisiones)} colisión(es) de WR después de normalizar: {muestras}."
            )

        sin_cliente = self._leer(
            "SELECT count(*) AS n FROM core_warehouse WHERE cliente_id IS NULL"
        )[0]["n"]
        if sin_cliente:
            # Aviso, no problema: ya está decidido qué hacer con ellas y el
            # migrador lo hace. Tratarlo como error abortaría el cutover por una
            # condición conocida y aceptada.
            self.reporte.avisos.append(
                f"{sin_cliente} cargas sin cliente asignado. "
                "Se migran con el creador de la empresa y quedan marcadas para revisión."
            )

        return not self.reporte.problemas

    # --- Etapas ---

    async def empresas(self) -> None:
        c = self.reporte.contador("companies")
        for fila in self._leer("SELECT id, name, created_at FROM core_company ORDER BY id"):
            c.leidos += 1
            if self.buscar("core_company", fila["id"]):
                c.ya_existian += 1
                continue

            nuevo = uuid4()
            if not self.seco:
                await self.session.execute(
                    text("""
                        INSERT INTO companies (id, legal_name, status, created_at)
                        VALUES (:id, :n, 'ACTIVE', :creada)
                    """),
                    {"id": nuevo, "n": fila["name"][:255], "creada": fila["created_at"]},
                )
            await self._registrar("core_company", fila["id"], "companies", nuevo)
            c.creados += 1

    async def usuarios(self) -> None:
        c = self.reporte.contador("users")
        filas = self._leer("""
            SELECT u.id, u.username, u.email, u.password, u.first_name, u.last_name,
                   u.is_active, u.is_staff, u.date_joined, u.last_login,
                   cp.company_id
            FROM auth_user u
            LEFT JOIN core_clientprofile cp ON cp.user_id = u.id
            ORDER BY u.id
        """)

        for fila in filas:
            c.leidos += 1
            if self.buscar("auth_user", fila["id"]):
                c.ya_existian += 1
                continue

            correo = (fila["email"] or "").strip()
            if not correo:
                # Sin correo no se puede migrar como cuenta con acceso. Se salta
                # y se reporta; la decisión es del Paso 5.1.
                self.reporte.problemas.append(
                    f"auth_user {fila['id']} ({fila['username']}) sin correo: no migrado."
                )
                continue

            nuevo = uuid4()
            if not self.seco:
                await self.session.execute(
                    text("""
                        INSERT INTO users
                            (id, email, password_hash, first_name, last_name, status,
                             created_at, last_login_at)
                        VALUES (:id, :e, :h, :n, :a, :estado, :alta, :ultimo)
                    """),
                    {
                        "id": nuevo,
                        "e": correo,
                        # El PBKDF2 de Django se copia tal cual y se convierte a
                        # Argon2id en el primer inicio de sesión (Paso 1.5). Así
                        # nadie tiene que reiniciar su contraseña.
                        "h": fila["password"],
                        "n": (fila["first_name"] or fila["username"])[:80],
                        "a": (fila["last_name"] or "")[:80] or "-",
                        "estado": "ACTIVE" if fila["is_active"] else "SUSPENDED",
                        "alta": fila["date_joined"],
                        "ultimo": fila["last_login"],
                    },
                )
                await self.session.execute(
                    text("""
                        INSERT INTO legacy_password_pending (user_id, legacy_algorithm)
                        VALUES (:u, :alg) ON CONFLICT DO NOTHING
                    """),
                    {"u": nuevo, "alg": (fila["password"] or "").split("$")[0][:40]},
                )
                await self._asignar_rol(nuevo, fila)

            await self._registrar("auth_user", fila["id"], "users", nuevo)
            c.creados += 1

    async def _asignar_rol(self, user_id: UUID, fila: dict[str, Any]) -> None:
        """Staff a operaciones, cliente a su empresa (ADR-0011).

        El staff NO recibe membresía de empresa: darle una lo convertiría en
        cliente de esa empresa.
        """
        if fila["is_staff"]:
            await self.session.execute(
                text("""
                    INSERT INTO user_role_assignments (user_id, role_id, scope_type, company_id)
                    SELECT :u, r.id, :s, NULL FROM roles r WHERE r.code = :rol
                    ON CONFLICT DO NOTHING
                """),
                {"u": user_id, "rol": RoleCode.OPS_ADMIN.value, "s": ScopeType.GLOBAL.value},
            )
            return

        empresa = self.buscar("core_company", fila["company_id"]) if fila["company_id"] else None
        if empresa is None:
            return

        await self.session.execute(
            text("""
                INSERT INTO user_role_assignments (user_id, role_id, scope_type, company_id)
                SELECT :u, r.id, :s, :c FROM roles r WHERE r.code = :rol
                ON CONFLICT DO NOTHING
            """),
            {
                "u": user_id,
                "rol": RoleCode.CLIENT_ADMIN.value,
                "s": ScopeType.ORGANIZATION.value,
                "c": empresa,
            },
        )
        await self.session.execute(
            text("""
                INSERT INTO company_memberships (company_id, user_id, status)
                VALUES (:c, :u, 'ACTIVE') ON CONFLICT DO NOTHING
            """),
            {"c": empresa, "u": user_id},
        )

    async def cargas(self, *, origen: UUID, destino: UUID, bodega_origen: UUID) -> None:
        c = self.reporte.contador("shipments")
        filas = self._leer("""
            SELECT w.wr_number, w.company_id, w.cliente_id, w.status, w.created_at,
                   w.invoice, w.tracking, w.po, w.container, w.shipper, w.carrier,
                   w.weight_kgs, w.weight_lbs, w.volumetric_kgs, w.foots,
                   EXISTS (SELECT 1 FROM core_dispatchrequestitem i
                            WHERE i.warehouse_id = w.wr_number) AS tiene_despacho
            FROM core_warehouse w
            ORDER BY w.created_at, w.wr_number
        """)

        for fila in filas:
            c.leidos += 1
            wr = fila["wr_number"]
            if self.buscar("core_warehouse", wr):
                c.ya_existian += 1
                continue

            empresa = self.buscar("core_company", fila["company_id"])
            if empresa is None:
                self.reporte.problemas.append(f"carga {wr}: su empresa no se migró.")
                continue

            creador = self.buscar("auth_user", fila["cliente_id"]) if fila["cliente_id"] else None
            revision = False
            nota: str | None = None

            if creador is None:
                # `created_by` es obligatorio. Se usa un usuario de operaciones y
                # se marca: la atribución no viene del legacy, es un relleno.
                creador = await self._usuario_de_sistema()
                revision = True
                nota = "Sin cliente en el legacy; atribuida a la cuenta de sistema."

            estado, revision_estado, nota_estado = self._traducir_estado(
                fila["status"], tiene_despacho=fila["tiene_despacho"]
            )
            revision = revision or revision_estado
            nota = nota_estado if nota is None else f"{nota} {nota_estado or ''}".strip()

            nuevo = uuid4()
            if not self.seco:
                await self.session.execute(
                    text("""
                        INSERT INTO shipments
                            (id, shipment_number, company_id, created_by, current_status_code,
                             origin_location_id, origin_facility_id, destination_location_id,
                             weight_kg, weight_lb, volumetric_weight_kg,
                             foots_cft, shipper, carrier, description,
                             legacy_review_required, legacy_status,
                             created_at, updated_at,
                             dispatched_at)
                        VALUES (:id, siguiente_shipment_number(), :c, :u, :estado,
                                :o, :bodega, :d, :peso, :peso_lb, :vol,
                                :cft, :shipper, :carrier, :desc, :revision, :legacy,
                                :creada, :creada, :despachada)
                    """),
                    {
                        "id": nuevo,
                        "c": empresa,
                        "u": creador,
                        "estado": estado.value,
                        "o": origen,
                        "bodega": bodega_origen,
                        "d": destino,
                        "peso": fila["weight_kgs"],
                        # Kilos y libras van por separado, como en el legacy:
                        # son dos lecturas distintas, no una conversión.
                        "peso_lb": fila["weight_lbs"],
                        "vol": fila["volumetric_kgs"],
                        "cft": fila["foots"],
                        "shipper": (fila["shipper"] or "")[:180] or None,
                        "carrier": (fila["carrier"] or "")[:180] or None,
                        "desc": self._descripcion(fila),
                        "revision": revision,
                        "legacy": fila["status"],
                        "creada": fila["created_at"],
                        "despachada": (
                            fila["created_at"] if estado == ShipmentStatus.DISPATCHED else None
                        ),
                    },
                )
                await self._referencias(nuevo, fila)
                await self._evento_de_migracion(nuevo, fila, estado, nota)
                await self._requisitos(nuevo, estado)

            await self._registrar("core_warehouse", wr, "shipments", nuevo, nota)
            c.creados += 1
            if revision:
                c.marcados_revision += 1

    async def _evento_de_migracion(
        self,
        shipment_id: UUID,
        fila: dict[str, Any],
        estado: ShipmentStatus,
        nota: str | None,
    ) -> None:
        """Deja constancia de que la carga viene del sistema viejo.

        Sin esto la línea de tiempo queda vacía y quien abre la carga no ve
        ninguna historia: ni cuándo entró, ni de dónde salió su estado. El
        legacy no guardaba un historial que se pueda reconstruir, así que se
        registra lo único que sí se sabe — su estado original y la fecha de
        alta — en vez de inventar una secuencia de hitos que nadie observó.
        """
        descripcion = (
            f"Migrada del sistema anterior. Estado original: {fila['status']}. "
            f"Warehouse Receipt: {fila['wr_number']}."
        )
        if nota:
            descripcion = f"{descripcion} {nota}"

        await self.session.execute(
            text("""
                INSERT INTO shipment_events
                    (shipment_id, event_type, to_status_code, title, description,
                     occurred_at, actor_user_id)
                VALUES (:s, :tipo, :estado, :titulo, :descripcion, :cuando, :actor)
            """),
            {
                "s": shipment_id,
                "tipo": EventType.CREATED.value,
                "estado": estado.value,
                "titulo": "Carga migrada del sistema anterior",
                "descripcion": descripcion,
                # La fecha de alta del legacy, no la de la migración: fechar
                # todo el historial el día del cutover haría que 241 cargas
                # aparezcan creadas el mismo día.
                "cuando": fila["created_at"],
                "actor": await self._usuario_de_sistema(),
            },
        )

    async def _requisitos(self, shipment_id: UUID, estado: ShipmentStatus) -> None:
        """Abre los documentos que el catálogo exige, solo si aún sirven.

        Una carga ya despachada o entregada no se puede detener por un documento
        faltante: abrirle requisitos sería inventar trabajo pendiente sobre algo
        que ya ocurrió. En las que siguen en bodega sí importa, porque el
        despacho los va a exigir.
        """
        if estado not in _ESTADOS_QUE_AUN_EXIGEN_DOCUMENTOS:
            return

        from app.modules.shipments import service as shipments

        await shipments.sincronizar_requisitos_del_catalogo(
            self.session, shipment_id=shipment_id, actor_user_id=await self._usuario_de_sistema()
        )

    def _traducir_estado(
        self, legacy: str, *, tiene_despacho: bool
    ) -> tuple[ShipmentStatus, bool, str | None]:
        """ADR-0002. Nunca se inventa un estado."""
        if legacy in _ESTADO_DIRECTO:
            return _ESTADO_DIRECTO[legacy], False, None

        if legacy == "APROBADO":
            if tiene_despacho:
                return ShipmentStatus.DISPATCH_REQUESTED, False, None
            # Sin solicitud de despacho que lo respalde, el mapeo sería una
            # suposición.
            return (
                ShipmentStatus.STORED,
                True,
                "APROBADO sin solicitud de despacho vinculada.",
            )

        if legacy == "PENDIENTE":
            # El legacy no guarda fecha de recepción, y tener documentos no
            # prueba recepción física.
            return ShipmentStatus.STORED, True, "PENDIENTE: estado real desconocido."

        return ShipmentStatus.STORED, True, f"Estado legacy no previsto: {legacy}."

    def _descripcion(self, fila: dict[str, Any]) -> str | None:
        """La descripción ya no repite lo que ahora son campos propios.

        La primera versión del migrador metía remitente, transportista y pies
        acá porque el esquema nuevo no tenía dónde ponerlos. Dentro de un texto
        libre no se pueden buscar ni ordenar, que es justamente para lo que se
        usan, así que ahora van a sus columnas y esto queda vacío salvo que el
        legacy traiga algo que no encaje en ningún campo.
        """
        return None

    async def _referencias(self, shipment_id: UUID, fila: dict[str, Any]) -> None:
        """El WR y los demás identificadores del legacy, como referencias.

        El WR deja de ser identidad (ADR-0005): la carga tiene su propio número
        y el WR es una forma más de buscarla.
        """
        referencias = [
            (ReferenceType.WR, normalizar_wr(fila["wr_number"])),
            (ReferenceType.INVOICE, (fila.get("invoice") or "").strip()),
            (ReferenceType.TRACKING, (fila.get("tracking") or "").strip()),
            (ReferenceType.PO, (fila.get("po") or "").strip()),
            (ReferenceType.CONTAINER, (fila.get("container") or "").strip()),
        ]

        for tipo, valor in referencias:
            if not valor:
                continue
            await self.session.execute(
                text("""
                    INSERT INTO shipment_references (shipment_id, reference_type, value)
                    VALUES (:s, :t, :v) ON CONFLICT DO NOTHING
                """),
                {"s": shipment_id, "t": tipo.value, "v": valor[:120]},
            )

    async def _usuario_de_sistema(self) -> UUID:
        """Cuenta a la que se atribuye lo que el legacy no atribuye a nadie.

        Se crea una sola vez y se marca claramente: que aparezca en el historial
        es preferible a inventar que la creó una persona concreta.
        """
        existente: UUID | None = (
            await self.session.execute(
                text("SELECT id FROM users WHERE email = :e"),
                {"e": "migracion@sistema.amvarmar.com"},
            )
        ).scalar_one_or_none()
        if existente:
            return existente

        nuevo = uuid4()
        if not self.seco:
            await self.session.execute(
                text("""
                    INSERT INTO users (id, email, password_hash, first_name, last_name, status)
                    VALUES (:id, :e, 'sin-acceso', 'Migración', 'Sistema', 'SUSPENDED')
                """),
                {"id": nuevo, "e": "migracion@sistema.amvarmar.com"},
            )
        return nuevo

    async def paquetes(self) -> None:
        """Migra cada `core_piecewarehouse` como una pieza con su cantidad.

        El esquema final del legacy no tiene `core_pieceitem` ni dimensiones
        por unidad. Consultar esa tabla hacía que el migrador fallara antes de
        leer la primera pieza. Se conserva exactamente la granularidad que sí
        existe: tipo, cantidad y descripción.
        """
        c = self.reporte.contador("shipment_packages")

        for fila in self._leer("""
            SELECT id, warehouse_id, type_of, quantity, description
            FROM core_piecewarehouse ORDER BY id
        """):
            c.leidos += 1
            if self.buscar("core_piecewarehouse", fila["id"]):
                c.ya_existian += 1
                continue

            carga = self.buscar("core_warehouse", fila["warehouse_id"])
            if carga is None:
                self.reporte.problemas.append(
                    f"pieza {fila['id']}: su carga {fila['warehouse_id']} no se migró."
                )
                continue

            tipo = _TIPO_BULTO.get((fila["type_of"] or "").upper().strip(), "OTHER")
            await self._insertar_bulto(
                carga,
                tipo=tipo,
                cantidad=fila["quantity"] or 1,
                descripcion=fila["description"],
                origen=("core_piecewarehouse", fila["id"]),
            )
            c.creados += 1

    async def _insertar_bulto(
        self,
        shipment_id: UUID,
        *,
        tipo: str,
        cantidad: int,
        descripcion: str | None,
        origen: tuple[str, Any],
        dimensiones: dict[str, Any] | None = None,
    ) -> None:
        nuevo = uuid4()
        if not self.seco:
            await self.session.execute(
                text("""
                    INSERT INTO shipment_packages
                        (id, shipment_id, package_type, quantity, description,
                         weight_kg, length_cm, width_cm, height_cm)
                    VALUES (:id, :s, :tipo, :cant, :desc, :peso, :largo, :ancho, :alto)
                """),
                {
                    "id": nuevo,
                    "s": shipment_id,
                    "tipo": tipo,
                    "cant": cantidad,
                    "desc": (descripcion or "")[:500] or None,
                    "peso": (dimensiones or {}).get("weight_kgs"),
                    "largo": (dimensiones or {}).get("length_cm"),
                    "ancho": (dimensiones or {}).get("width_cm"),
                    "alto": (dimensiones or {}).get("height_cm"),
                },
            )
        await self._registrar(origen[0], origen[1], "shipment_packages", nuevo)

    async def despachos(self) -> None:
        c = self.reporte.contador("dispatch_requests")
        for fila in self._leer("""
            SELECT id, company_id, user_id, method, status, created_at
            FROM core_dispatchrequest ORDER BY id
        """):
            c.leidos += 1
            if self.buscar("core_dispatchrequest", fila["id"]):
                c.ya_existian += 1
                continue

            empresa = self.buscar("core_company", fila["company_id"])
            solicitante = self.buscar("auth_user", fila["user_id"]) if fila["user_id"] else None
            if empresa is None or solicitante is None:
                self.reporte.problemas.append(
                    f"despacho {fila['id']}: falta su empresa o su solicitante."
                )
                continue

            estado = _ESTADO_DESPACHO.get((fila["status"] or "").upper().strip())
            if estado is None:
                self.reporte.problemas.append(
                    f"despacho {fila['id']}: estado legacy desconocido {fila['status']!r}."
                )
                continue
            metodo = _METODO.get((fila["method"] or "").upper().strip())
            if metodo is None:
                self.reporte.problemas.append(
                    f"despacho {fila['id']}: método legacy desconocido {fila['method']!r}."
                )
                continue
            nuevo = uuid4()
            if not self.seco:
                await self.session.execute(
                    text("""
                        INSERT INTO dispatch_requests
                            (id, dispatch_number, company_id, requested_by, method, status,
                             requested_at, updated_at, approved_at, dispatched_at,
                             completed_at, rejected_reason)
                        VALUES (:id, siguiente_dispatch_number(), :c, :u, :m, :estado,
                                :creada, :creada, :aprobada, :despachada,
                                :completada, :motivo)
                    """),
                    {
                        "id": nuevo,
                        "c": empresa,
                        "u": solicitante,
                        "m": metodo,
                        "estado": estado,
                        "creada": fila["created_at"],
                        # Se decide acá y no con un CASE en SQL: usar el mismo
                        # parámetro como valor de columna y en una comparación
                        # impide que asyncpg deduzca un tipo único.
                        "aprobada": (
                            fila["created_at"] if estado in {"APPROVED", "COMPLETED"} else None
                        ),
                        "despachada": (fila["created_at"] if estado == "COMPLETED" else None),
                        "completada": (fila["created_at"] if estado == "COMPLETED" else None),
                        # El esquema nuevo exige motivo al rechazar y el legacy
                        # no lo guardaba. Se deja dicho que falta, en vez de
                        # inventar uno: un motivo fabricado sería peor que la
                        # ausencia, porque parecería información real.
                        "motivo": (
                            "Rechazado en el sistema anterior, que no registraba el motivo."
                            if estado == "REJECTED"
                            else None
                        ),
                    },
                )
            await self._registrar("core_dispatchrequest", fila["id"], "dispatch_requests", nuevo)
            c.creados += 1

    async def cargas_de_despacho(self) -> None:
        c = self.reporte.contador("dispatch_request_shipments")
        for fila in self._leer("""
            SELECT id, dispatch_id, warehouse_id FROM core_dispatchrequestitem ORDER BY id
        """):
            c.leidos += 1
            if self.buscar("core_dispatchrequestitem", fila["id"]):
                c.ya_existian += 1
                continue

            despacho = self.buscar("core_dispatchrequest", fila["dispatch_id"])
            carga = self.buscar("core_warehouse", fila["warehouse_id"])
            if despacho is None or carga is None:
                self.reporte.problemas.append(f"ítem de despacho {fila['id']}: falta una punta.")
                continue

            if not self.seco:
                # `released_at` se llena en los despachos ya cerrados: si no, el
                # índice único de reclamo activo impediría que una carga
                # aparezca en dos despachos históricos.
                await self.session.execute(
                    text("""
                        INSERT INTO dispatch_request_shipments
                            (dispatch_request_id, shipment_id, released_at)
                        SELECT :d, :s,
                               CASE WHEN dr.status IN ('COMPLETED','REJECTED','CANCELLED')
                                    THEN dr.requested_at END
                        FROM dispatch_requests dr WHERE dr.id = :d
                        ON CONFLICT DO NOTHING
                    """),
                    {"d": despacho, "s": carga},
                )
            await self._registrar(
                "core_dispatchrequestitem", fila["id"], "dispatch_request_shipments", despacho
            )
            c.creados += 1

    async def documentos(self) -> None:
        """Registra adjuntos sin tipo demostrable como `LEGACY_UNCLASSIFIED`.

        `core_warehousedocument` no guarda un catálogo funcional: `cont_type`
        es el MIME del archivo, no su clase de negocio. Llamarlos factura
        comercial abría o satisfacía requisitos con una evidencia inventada.
        """
        c = self.reporte.contador("documents")
        tipo_legacy = (
            await self.session.execute(
                text("SELECT id FROM document_types WHERE code = 'LEGACY_UNCLASSIFIED'")
            )
        ).scalar_one_or_none()

        for fila in self._leer("""
            SELECT id, warehouse_id, file, original_name, cont_type, size_bytes,
                   uploaded_at, uploaded_by_id
            FROM core_warehousedocument ORDER BY id
        """):
            c.leidos += 1
            if self.buscar("core_warehousedocument", fila["id"]):
                c.ya_existian += 1
                continue

            carga = self.buscar("core_warehouse", fila["warehouse_id"])
            if carga is None or tipo_legacy is None:
                self.reporte.problemas.append(
                    f"documento {fila['id']}: su carga {fila['warehouse_id']} no se migró."
                )
                continue

            await self._registrar_documento(
                fila,
                carga=carga,
                tipo_id=tipo_legacy,
                tabla_legacy="core_warehousedocument",
                issued_by="OTHER",
            )
            c.creados += 1

    async def documentos_de_despacho(self) -> None:
        """Separa documentos de carga y documentos globales de despacho.

        La factura sigue perteneciendo a su carga. El BL pertenece únicamente
        al despacho, aunque el legacy guardara además `warehouse_id` para
        producir una fila por carga. Crear ambos enlaces reintroduciría la fuga
        de contexto que el modelo nuevo prohíbe.
        """
        for tabla, code, campo_fecha in (
            ("core_warehouseinvoice", "COMMERCIAL_INVOICE", "uploaded_at"),
            ("core_dispatchbldocument", "BL", "uploaded_at"),
        ):
            c = self.reporte.contador(f"documents ({code})")
            tipo_id = (
                await self.session.execute(
                    text("SELECT id FROM document_types WHERE code = :c"), {"c": code}
                )
            ).scalar_one_or_none()

            # El nombre de tabla y de columna salen de la tupla literal de
            # arriba, no de datos: la interpolación es la única forma de leer
            # dos tablas con la misma consulta.
            consulta = f"""
                SELECT id, warehouse_id, dispatch_id, file, original_name,
                       content_type, size_bytes, {campo_fecha} AS subido, uploaded_by_id
                FROM {tabla} ORDER BY id
            """  # noqa: S608

            for fila in self._leer(consulta):
                c.leidos += 1
                if self.buscar(tabla, fila["id"]):
                    c.ya_existian += 1
                    continue

                carga = self.buscar("core_warehouse", fila["warehouse_id"])
                despacho = (
                    self.buscar("core_dispatchrequest", fila["dispatch_id"])
                    if fila["dispatch_id"]
                    else None
                )

                padre = despacho if code == "BL" else carga
                if padre is None or tipo_id is None:
                    self.reporte.problemas.append(
                        f"{tabla} {fila['id']}: falta su "
                        f"{'despacho' if code == 'BL' else 'carga'} o el tipo {code}."
                    )
                    continue

                if code == "BL" and despacho is not None:
                    await self._registrar_documento_despacho(
                        fila,
                        despacho=despacho,
                        tipo_id=tipo_id,
                        tabla_legacy=tabla,
                        issued_by="CARRIER",
                    )
                elif carga is not None:
                    await self._registrar_documento(
                        fila,
                        carga=carga,
                        tipo_id=tipo_id,
                        tabla_legacy=tabla,
                        issued_by="OTHER",
                    )

                c.creados += 1

    async def adjuntos_de_carga(self) -> None:
        """El Warehouse Receipt que cuelga directo de la carga.

        `core_warehouse.uploaded_file` no pasa por `core_warehousedocument`: es
        un campo de archivo en la propia fila del warehouse, y por eso se
        escapaba de las otras fases. Son 221 archivos y casi 10 GB — el grueso
        del archivo histórico—, así que dejarlos afuera vaciaba el expediente de
        casi todas las cargas migradas.

        Se registra con una clave propia (`core_warehouse.uploaded_file`) y no
        con `core_warehouse` a secas: esa ya está tomada por el mapeo de la
        carga, y reusarla pisaría el id del shipment con el del documento.
        """
        c = self.reporte.contador("documents (WAREHOUSE_RECEIPT)")
        tipo_id = (
            await self.session.execute(
                text("SELECT id FROM document_types WHERE code = 'WAREHOUSE_RECEIPT'")
            )
        ).scalar_one_or_none()

        for fila in self._leer("""
            SELECT wr_number, uploaded_file, created_at
            FROM core_warehouse
            WHERE uploaded_file IS NOT NULL AND uploaded_file <> ''
            ORDER BY wr_number
        """):
            c.leidos += 1
            clave = "core_warehouse.uploaded_file"
            if self.buscar(clave, fila["wr_number"]):
                c.ya_existian += 1
                continue

            carga = self.buscar("core_warehouse", fila["wr_number"])
            if carga is None or tipo_id is None:
                self.reporte.problemas.append(
                    f"adjunto de {fila['wr_number']}: su carga no se migró o falta el tipo."
                )
                continue

            await self._registrar_documento(
                {
                    "id": fila["wr_number"],
                    "file": fila["uploaded_file"],
                    # El nombre del archivo es todo lo que hay: la fila del
                    # warehouse no guarda nombre original ni tipo ni tamaño. Los
                    # tres reales los calcula el Paso 5.3 al subir el objeto.
                    "original_name": fila["uploaded_file"].rsplit("/", 1)[-1],
                    "content_type": None,
                    "size_bytes": None,
                    "subido": fila["created_at"],
                    "uploaded_by_id": None,
                },
                carga=carga,
                tipo_id=tipo_id,
                tabla_legacy=clave,
                issued_by="AMVARMAR",
            )
            c.creados += 1

    async def _registrar_documento(
        self,
        fila: dict[str, Any],
        *,
        carga: UUID,
        tipo_id: UUID,
        tabla_legacy: str,
        issued_by: str,
    ) -> UUID:
        """Crea el documento y su enlace con la carga.

        Queda en `UPLOADING` con la ruta del legacy: el archivo todavía vive en
        el servidor viejo y subirlo al storage privado es el Paso 5.3.
        """
        empresa = (
            (
                await self.session.execute(
                    text("SELECT company_id FROM shipments WHERE id = :s"), {"s": carga}
                )
            ).scalar_one_or_none()
            if not self.seco
            else None
        )

        nuevo = await self._crear_documento_legacy(
            fila,
            empresa=empresa,
            tabla_legacy=tabla_legacy,
            issued_by=issued_by,
        )
        if not self.seco:
            await self.session.execute(
                text("""
                    INSERT INTO shipment_documents (shipment_id, document_id, document_type_id)
                    VALUES (:s, :d, :t) ON CONFLICT DO NOTHING
                """),
                {"s": carga, "d": nuevo, "t": tipo_id},
            )
        return nuevo

    async def _registrar_documento_despacho(
        self,
        fila: dict[str, Any],
        *,
        despacho: UUID,
        tipo_id: UUID,
        tabla_legacy: str,
        issued_by: str,
    ) -> UUID:
        empresa = (
            (
                await self.session.execute(
                    text("SELECT company_id FROM dispatch_requests WHERE id = :d"),
                    {"d": despacho},
                )
            ).scalar_one_or_none()
            if not self.seco
            else None
        )
        nuevo = await self._crear_documento_legacy(
            fila,
            empresa=empresa,
            tabla_legacy=tabla_legacy,
            issued_by=issued_by,
        )
        if not self.seco:
            await self.session.execute(
                text("""
                    INSERT INTO dispatch_documents
                        (dispatch_request_id, document_id, document_type_id)
                    VALUES (:d, :doc, :t) ON CONFLICT DO NOTHING
                """),
                {"d": despacho, "doc": nuevo, "t": tipo_id},
            )
        return nuevo

    async def _crear_documento_legacy(
        self,
        fila: dict[str, Any],
        *,
        empresa: UUID | None,
        tabla_legacy: str,
        issued_by: str,
    ) -> UUID:
        subido_por = (
            self.buscar("auth_user", fila["uploaded_by_id"]) if fila.get("uploaded_by_id") else None
        ) or await self._usuario_de_sistema()
        nombre = str(fila.get("original_name") or fila.get("file") or "documento")[:255]
        nuevo = uuid4()
        if not self.seco:
            if empresa is None:
                raise RuntimeError(f"{tabla_legacy} {fila['id']}: no se pudo resolver la empresa.")
            await self.session.execute(
                text("""
                    INSERT INTO documents
                        (id, company_id, uploaded_by, storage_provider, storage_key,
                         original_name, safe_name, media_type, size_bytes, sha256,
                         upload_status, issued_by, created_at)
                    VALUES (:id, :c, :u, 'legacy', :ruta, :nombre, :seguro,
                            :tipo, :tam, :hash, 'UPLOADING', :issued_by, :subido)
                """),
                {
                    "id": nuevo,
                    "c": empresa,
                    "u": subido_por,
                    "ruta": str(fila.get("file") or "")[:500],
                    "nombre": nombre,
                    "seguro": nombre_seguro(nombre),
                    "tipo": str(
                        fila.get("content_type")
                        or fila.get("cont_type")
                        or "application/octet-stream"
                    )[:150],
                    "tam": fila.get("size_bytes") or 1,
                    "hash": "0" * 64,
                    "issued_by": issued_by,
                    "subido": fila.get("subido") or fila.get("uploaded_at"),
                },
            )

        await self._registrar(tabla_legacy, fila["id"], "documents", nuevo)
        return nuevo

    async def ejecutar(self) -> Reporte:
        await self._cargar_mapa()
        if not await self.comprobar():
            return self.reporte

        origen, destino, bodega_origen = await self._ubicaciones()

        await self.empresas()
        await self.usuarios()
        await self.cargas(origen=origen, destino=destino, bodega_origen=bodega_origen)
        await self.paquetes()
        await self.despachos()
        await self.cargas_de_despacho()
        await self.documentos()
        await self.documentos_de_despacho()
        await self.adjuntos_de_carga()
        return self.reporte

    async def _ubicaciones(self) -> tuple[UUID, UUID, UUID]:
        """El legacy no guarda origen ni destino por carga.

        Se usan Miami y San José, que es la ruta del negocio. Queda como dato
        por revisar si algún día hay más rutas: no se inventó por carga, se puso
        la única que el sistema viejo manejaba.
        """
        ids = []
        for pais, ciudad, nombre in (("US", "MIA", "Miami"), ("CR", "SJO", "San José")):
            ids.append(
                (
                    await self.session.execute(
                        text("""
                            INSERT INTO locations (country_code, city_code, location_code, name)
                            VALUES (:p, :c, :cod, :n)
                            ON CONFLICT (location_code) DO UPDATE SET name = EXCLUDED.name
                            RETURNING id
                        """),
                        {"p": pais, "c": ciudad, "cod": f"{pais}-{ciudad}", "n": nombre},
                    )
                ).scalar_one()
            )
        bodega = (
            await self.session.execute(
                text("""
                    SELECT id FROM facilities
                    WHERE location_id = :ubicacion AND uses_warehouse_receipt
                    ORDER BY facility_code
                    LIMIT 1
                """),
                {"ubicacion": ids[0]},
            )
        ).scalar_one_or_none()
        if bodega is None:
            bodega = (
                await self.session.execute(
                    text("""
                        INSERT INTO facilities
                            (location_id, facility_code, facility_type,
                             uses_warehouse_receipt, is_active)
                        VALUES (:ubicacion, 'MIA-LEGACY', 'WAREHOUSE', true, true)
                        ON CONFLICT (facility_code) DO UPDATE
                        SET location_id = EXCLUDED.location_id,
                            uses_warehouse_receipt = true,
                            is_active = true
                        RETURNING id
                    """),
                    {"ubicacion": ids[0]},
                )
            ).scalar_one()
        return ids[0], ids[1], bodega


async def principal() -> None:
    parser = argparse.ArgumentParser(description="Migra el sistema viejo al nuevo.")
    parser.add_argument(
        "--dry-run", action="store_true", help="No escribe nada; solo dice qué haría."
    )
    parser.add_argument(
        "--legacy-url",
        default=LEGACY_URL_POR_DEFECTO,
        help="Conexión a la base vieja.",
    )
    argumentos = parser.parse_args()

    with psycopg.connect(argumentos.legacy_url) as legacy:
        async with get_sessionmaker()() as session:
            migrador = Migrador(legacy, session, seco=argumentos.dry_run)
            reporte = await migrador.ejecutar()

            if argumentos.dry_run:
                # Se revierte explícitamente aunque no se haya escrito: si una
                # etapa futura olvidara comprobar `seco`, esto lo contiene.
                await session.rollback()
            else:
                await session.commit()

    await get_engine().dispose()
    reporte.imprimir(seco=argumentos.dry_run)

    if reporte.problemas:
        # Solo los problemas cortan. Los avisos se leen y se siguen.
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(principal())
