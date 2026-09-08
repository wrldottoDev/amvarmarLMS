"""Base de conocimiento de `como_hago` (ADR-0012, Fase 3).

Cada archivo en `copilot/conocimiento/*.md` (salvo los que empiezan con `_`,
que son notas internas — ver `_pendientes.md`) es una entrada: primera línea
`# Título`, segunda línea un comentario HTML `<!-- palabras_clave: a, b, c -->`,
y el resto del archivo es la respuesta en Markdown tal cual se la mostramos.

Todo el contenido sale de `docs/reescritura/04-reglas-negocio-objetivo.md` y
de los componentes reales del frontend — nada de esto es un procedimiento
inventado. Un tema que no matchea ninguna entrada NO se contesta con una
adivinanza: el ejecutor (`executors.como_hago`) devuelve `tiene_respuesta:
False`, igual que antes de que existiera esta base.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

_DIRECTORIO = Path(__file__).parent / "conocimiento"
_PATRON_PALABRAS_CLAVE = re.compile(r"<!--\s*palabras_clave:\s*(.*?)\s*-->")

# Palabras demasiado comunes para decidir un match — sin filtrarlas, una
# pregunta cualquiera comparte "que"/"de"/"una" con casi cualquier entrada.
_PALABRAS_VACIAS = frozenset((
    "que", "de", "la", "el", "los", "las", "una", "un", "unos", "unas", "y", "o", "en",
    "no", "mi", "se", "del", "al", "con", "por", "para", "es", "son", "como", "hago",
    "hace", "mis", "tu", "su", "sus", "esta", "este", "esa", "ese", "lo", "le", "les",
))  # fmt: skip


@dataclass(frozen=True)
class EntradaConocimiento:
    titulo: str
    palabras_clave: tuple[str, ...]
    cuerpo: str


def _normalizar(texto: str) -> str:
    """Minúsculas y sin acentos, para que "cómo" y "como" matcheen igual."""
    sin_acentos = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode("ascii")
    return sin_acentos.lower()


def _parsear(ruta: Path) -> EntradaConocimiento:
    contenido = ruta.read_text(encoding="utf-8")
    lineas = contenido.splitlines()
    titulo = lineas[0].removeprefix("# ").strip()

    coincidencia = _PATRON_PALABRAS_CLAVE.search(contenido)
    if coincidencia is None:
        raise ValueError(f"{ruta.name}: falta el comentario de palabras_clave.")
    palabras_clave = tuple(p.strip() for p in coincidencia.group(1).split(",") if p.strip())

    cuerpo = _PATRON_PALABRAS_CLAVE.sub("", contenido, count=1).replace(lineas[0], "", 1).strip()
    return EntradaConocimiento(titulo=titulo, palabras_clave=palabras_clave, cuerpo=cuerpo)


@lru_cache
def _entradas() -> tuple[EntradaConocimiento, ...]:
    archivos = sorted(p for p in _DIRECTORIO.glob("*.md") if not p.name.startswith("_"))
    return tuple(_parsear(p) for p in archivos)


def _palabras(texto: str) -> frozenset[str]:
    # `findall` en vez de `.split()`: sin esto, "despacho?" o "carga," nunca
    # matchean la palabra clave "despacho"/"carga" por el signo pegado.
    return frozenset(re.findall(r"[a-z0-9]+", _normalizar(texto))) - _PALABRAS_VACIAS


@lru_cache
def _palabras_clave_de(entrada: EntradaConocimiento) -> frozenset[str]:
    """Todas las palabras de todas las frases clave, sueltas — así "carga
    nueva" en la pregunta matchea la palabra clave "nueva carga" aunque el
    orden no coincida, y "creo una carga" matchea "crear carga" en la palabra
    que sí comparten (`carga`) sin depender de la conjugación del verbo."""
    palabras: set[str] = set()
    for clave in entrada.palabras_clave:
        palabras |= _palabras(clave)
    return frozenset(palabras)


def buscar(tema: str) -> EntradaConocimiento | None:
    """La entrada que más palabras comparte con `tema` (bolsa de palabras,
    no frase exacta). `None` si ninguna comparte al menos una — un empate se
    resuelve por orden alfabético de archivo, para que el resultado sea el
    mismo en cada corrida."""
    palabras_tema = _palabras(tema)
    mejor: EntradaConocimiento | None = None
    mejor_puntaje = 0
    for entrada in _entradas():
        puntaje = len(palabras_tema & _palabras_clave_de(entrada))
        if puntaje > mejor_puntaje:
            mejor, mejor_puntaje = entrada, puntaje
    return mejor
