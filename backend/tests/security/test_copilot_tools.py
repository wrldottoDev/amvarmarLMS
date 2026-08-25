"""Autorización de las herramientas del asistente (ADR-0012).

El asistente NUNCA es la frontera de autorización. Estos tests fijan que el
filtro previo y la verificación al ejecutar son dos controles distintos.
"""

from uuid import uuid4

import pytest

from app.modules.copilot.tools import (
    HERRAMIENTAS,
    ClaseHerramienta,
    esquema_para_proveedor,
    herramientas_disponibles,
    puede_ejecutar,
)
from app.modules.rbac.catalog import ROLES, Perm
from app.modules.rbac.models import RoleCode
from app.modules.rbac.service import PermisoEfectivo, PermisosEfectivos

pytestmark = pytest.mark.security


def _permisos(*codigos: str) -> PermisosEfectivos:
    return PermisosEfectivos(
        user_id=uuid4(),
        authz_version=1,
        permisos=tuple(
            PermisoEfectivo(code=c, scope_type="GLOBAL", company_id=None) for c in codigos
        ),
    )


def _permisos_de_rol(rol: str) -> PermisosEfectivos:
    return _permisos(*ROLES[rol].permissions)


class TestFiltradoPorPermiso:
    def test_un_cliente_no_ve_la_herramienta_de_ocr(self) -> None:
        disponibles = {
            h.nombre for h in herramientas_disponibles(_permisos_de_rol(RoleCode.CLIENT_USER))
        }

        assert "procesar_factura_ocr" not in disponibles
        assert "consultar_estado_carga" in disponibles

    def test_operaciones_ve_todas(self) -> None:
        disponibles = {
            h.nombre for h in herramientas_disponibles(_permisos_de_rol(RoleCode.OPS_ADMIN))
        }

        assert disponibles == set(HERRAMIENTAS)

    def test_sin_permisos_no_hay_herramientas(self) -> None:
        assert herramientas_disponibles(_permisos()) == []


class TestVerificacionAlEjecutar:
    """El control que realmente protege.

    Un modelo puede emitir una llamada a una herramienta que no se le ofreció;
    si el ejecutor confiara en el filtro previo, esa llamada se ejecutaría.
    """

    def test_una_herramienta_no_ofrecida_no_se_ejecuta(self) -> None:
        cliente = _permisos_de_rol(RoleCode.CLIENT_USER)

        # El modelo "alucina" una llamada que nunca estuvo en su lista.
        assert puede_ejecutar("procesar_factura_ocr", cliente) is False

    def test_una_herramienta_inventada_no_se_ejecuta(self) -> None:
        assert (
            puede_ejecutar("borrar_todas_las_cargas", _permisos_de_rol(RoleCode.SUPER_ADMIN))
            is False
        )

    def test_con_el_permiso_si_se_ejecuta(self) -> None:
        assert (
            puede_ejecutar("procesar_factura_ocr", _permisos(Perm.DOCUMENTS_UPLOAD_INTERNAL))
            is True
        )

    @pytest.mark.parametrize("nombre", list(HERRAMIENTAS))
    def test_ninguna_herramienta_pasa_sin_permisos(self, nombre: str) -> None:
        assert puede_ejecutar(nombre, _permisos()) is False


class TestContratoDelCatalogo:
    def test_el_asistente_no_amplia_lo_que_el_usuario_ya_podia_hacer(self) -> None:
        """Cada herramienta exige el permiso de la operación que realiza, no uno
        propio del copiloto: el asistente es otra vía a lo mismo, no un atajo."""
        permisos_de_negocio = set(
            __import__("app.modules.rbac.catalog", fromlist=["PERMISSIONS"]).PERMISSIONS
        )

        for definicion in HERRAMIENTAS.values():
            assert definicion.permiso in permisos_de_negocio

    def test_toda_herramienta_de_escritura_esta_marcada(self) -> None:
        """Lo marcado como ESCRITURA pasa por confirmación humana; que una acción
        que modifica datos quede como LECTURA la saltearía."""
        escritura = {
            nombre for nombre, d in HERRAMIENTAS.items() if d.clase is ClaseHerramienta.ESCRITURA
        }

        assert escritura == {"procesar_factura_ocr", "crear_prealerta_borrador"}

    def test_ninguna_herramienta_acepta_company_id(self) -> None:
        """El alcance sale del JWT. Si el modelo pudiera elegir la empresa,
        bastaría convencerlo con texto para leer datos ajenos."""
        for definicion in HERRAMIENTAS.values():
            campos = set(definicion.argumentos.model_fields)
            assert "company_id" not in campos
            assert "empresa_id" not in campos

    def test_el_esquema_sale_del_modelo_pydantic(self) -> None:
        """Generarlo desde Pydantic evita que el esquema que ve la IA y el que
        valida la entrada puedan divergir."""
        esquema = esquema_para_proveedor(HERRAMIENTAS["cotizar_envio"])

        assert esquema["function"]["name"] == "cotizar_envio"
        assert esquema["function"]["strict"] is True
        assert "peso_kg" in esquema["function"]["parameters"]["properties"]
