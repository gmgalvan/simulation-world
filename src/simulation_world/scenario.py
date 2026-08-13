"""Orders of battle read from a YAML file.

The command-line options set one count per unit type and apply it to both
sides, which is fine for a quick symmetric skirmish but cannot express the
interesting cases: a landing against a dug-in defender, armour against
infantry, a fleet against a coast. Nine unit types across two teams is
eighteen numbers, well past what belongs on a command line, so an asymmetric
order of battle wants a file.

The file is the scenario; the command line stays as the symmetric shortcut.
Both end up as the same roster structure, so `Battle` only knows one way to be
told what to deploy.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from pathlib import Path

import yaml

from .entities import SPECS

# Team keys accepted at the top level of the file.
TEAM_KEYS = {"rojo": 0, "red": 0, "azul": 1, "blue": 1}

# Spanish names for the unit types, so a scenario reads like the HUD and the
# battle report rather than like the internals. The English keys used inside
# the simulation are accepted too.
KIND_ALIASES = {
    "caza": "jet",
    "avion": "jet",
    "helicoptero": "helicopter",
    "heli": "helicopter",
    "convertiplano": "osprey",
    "v22": "osprey",
    "tanque": "tank",
    "antiaerea": "sam",
    "aa": "sam",
    "rpg": "rocket",
    "cohete": "rocket",
    "fusilero": "rifleman",
    "destructor": "destroyer",
    "submarino": "submarine",
}


class ScenarioError(ValueError):
    """A scenario file that cannot be honoured, reported with its cause."""


def _normalise(text: str) -> str:
    """Fold a key to lowercase ASCII, so `helicóptero` matches `helicoptero`."""
    stripped = unicodedata.normalize("NFKD", str(text).strip().lower())
    return "".join(ch for ch in stripped if not unicodedata.combining(ch))


def _resolve_kind(key: str) -> str:
    name = _normalise(key)
    kind = KIND_ALIASES.get(name, name)
    if kind not in SPECS:
        known = ", ".join(sorted(KIND_ALIASES))
        raise ScenarioError(f"unidad desconocida: {key!r}. Se admiten: {known}")
    return kind


def _resolve_count(kind: str, value) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ScenarioError(
            f"la cantidad de {kind!r} debe ser un numero entero, no {value!r}"
        )
    if value < 0:
        raise ScenarioError(f"la cantidad de {kind!r} no puede ser negativa: {value}")
    return value


@dataclass(frozen=True)
class Scenario:
    """A named order of battle, plus whatever else the file chooses to fix."""

    name: str
    roster: dict[int, dict[str, int]]
    seed: int | None = None
    city: bool | None = None
    # Which side the city belongs to. None leaves it to the seed, which is
    # what happens without a scenario — fine for a symmetric skirmish, useless
    # for a landing, where the whole point is who is defending the place.
    city_team: int | None = None

    def total(self, team: int) -> int:
        return sum(self.roster.get(team, {}).values())

    def summary(self) -> str:
        lines = [f"escenario: {self.name}"]
        for team, label in ((0, "Rojo"), (1, "Azul")):
            counts = self.roster.get(team, {})
            listed = "  ".join(
                f"{kind} {count}" for kind, count in sorted(counts.items()) if count
            )
            lines.append(f"  {label}: {listed or '(sin unidades)'}")
        if self.city_team is not None:
            lines.append(f"  ciudad defendida por {('Rojo', 'Azul')[self.city_team]}")
        return "\n".join(lines)


def symmetric_roster(counts: dict[str, int]) -> dict[int, dict[str, int]]:
    """Give both sides the same force — what the command-line options mean."""
    return {team: dict(counts) for team in (0, 1)}


def load(path: str | Path) -> Scenario:
    """Read a scenario file, or raise ScenarioError explaining what is wrong."""
    path = Path(path)
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise ScenarioError(f"no existe el escenario: {path}") from error
    except yaml.YAMLError as error:
        raise ScenarioError(f"{path} no es YAML valido: {error}") from error

    if not isinstance(raw, dict):
        raise ScenarioError(f"{path} deberia contener un mapa en el nivel superior")

    header = raw.get("escenario") or raw.get("scenario") or {}
    if not isinstance(header, dict):
        raise ScenarioError("la seccion 'escenario' deberia ser un mapa")

    roster: dict[int, dict[str, int]] = {0: {}, 1: {}}
    seen_team = False
    for key, value in raw.items():
        team = TEAM_KEYS.get(_normalise(key))
        if team is None:
            continue
        seen_team = True
        if not isinstance(value, dict):
            raise ScenarioError(f"la seccion {key!r} deberia listar unidad: cantidad")
        for unit_key, count in value.items():
            kind = _resolve_kind(unit_key)
            roster[team][kind] = roster[team].get(kind, 0) + _resolve_count(kind, count)

    if not seen_team:
        raise ScenarioError(
            f"{path} no define ningun bando. Se esperaba una seccion 'rojo' y otra 'azul'"
        )
    # A side with nothing on it loses the instant the battle starts, which is
    # never what someone meant to write.
    for team, label in ((0, "rojo"), (1, "azul")):
        if not sum(roster[team].values()):
            raise ScenarioError(f"el bando {label!r} se queda sin unidades")

    seed = header.get("semilla", header.get("seed"))
    if seed is not None and (isinstance(seed, bool) or not isinstance(seed, int)):
        raise ScenarioError(f"la semilla deberia ser un entero, no {seed!r}")
    city, city_team = _resolve_city(header.get("ciudad", header.get("city")))

    return Scenario(
        name=str(header.get("nombre", header.get("name", path.stem))),
        roster=roster,
        seed=seed,
        city=city,
        city_team=city_team,
    )


def _resolve_city(value) -> tuple[bool | None, int | None]:
    """Read `ciudad:` as on/off, or as the name of the side that defends it.

    Naming a team is the useful form: `ciudad: azul` says Blue owns the place
    and Red is coming for it. Plain `true` leaves the owner to the seed.
    """
    if value is None:
        return None, None
    if isinstance(value, bool):
        return value, None
    team = TEAM_KEYS.get(_normalise(value))
    if team is None:
        raise ScenarioError(
            f"'ciudad' deberia ser true, false, 'rojo' o 'azul', no {value!r}"
        )
    return True, team
