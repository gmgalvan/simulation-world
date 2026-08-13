"""Battle bookkeeping: who shot what, what connected, and who killed whom.

Kept out of ``battle`` on purpose. The battle module is already the busiest
file in the project, and counting things has nothing to do with running a
simulation — it only ever observes.
"""

from __future__ import annotations

import collections
import datetime
import json
from pathlib import Path

# Display order and labels, so the report reads the same way every time.
KIND_LABELS = {
    "jet": "caza F-35",
    "helicopter": "helicoptero Mi-24",
    "osprey": "convertiplano V-22",
    "destroyer": "destructor",
    "submarine": "submarino",
    "tank": "tanque Leopard 2",
    "sam": "bateria antiaerea",
    "rocket": "equipo de RPG",
    "rifleman": "fusilero",
    "building": "edificio civil",
    "civilian_car": "coche civil",
    "civilian": "civil",
}
TEAM_LABELS = {0: "Rojo", 1: "Azul"}


class BattleStats:
    """Counts everything a battle does, then writes it out as a report."""

    def __init__(self, seed: int) -> None:
        self.seed = seed
        self.started = datetime.datetime.now()
        self.shots: collections.Counter = collections.Counter()
        self.hits: collections.Counter = collections.Counter()
        self.damage: collections.Counter = collections.Counter()
        self.kills: collections.Counter = collections.Counter()
        self.weapon_kills: collections.Counter = collections.Counter()
        self.salvos: collections.Counter = collections.Counter()
        self.missiles: collections.Counter = collections.Counter()
        self.intercepts: collections.Counter = collections.Counter()
        self.deployed: collections.Counter = collections.Counter()
        self.city: dict | None = None
        self.elapsed = 0.0
        self.winner: int | None = None

    # ------------------------------------------------------------------
    # Recording
    # ------------------------------------------------------------------
    def deploy(self, kind: str, team: int) -> None:
        self.deployed[(team, kind)] += 1

    def shot(self, kind: str, team: int) -> None:
        self.shots[(team, kind)] += 1

    def missile(self, kind: str, team: int, weapon: str) -> None:
        """A guided round left the rail; `weapon` names its class."""
        self.missiles[(team, kind, weapon)] += 1

    def salvo(self, kind: str, team: int, count: int) -> None:
        self.salvos[(team, kind)] += count

    def intercept(self, kind: str, team: int, weapon: str) -> None:
        """An incoming guided weapon was destroyed before reaching its target."""
        self.intercepts[(team, kind, weapon)] += 1

    def hit(self, shooter_kind: str, target_kind: str, team: int, amount: float) -> None:
        self.hits[(shooter_kind, target_kind)] += 1
        self.damage[(shooter_kind, target_kind)] += amount

    def kill(
        self,
        shooter_kind: str,
        target_kind: str,
        team: int,
        weapon: str | None = None,
    ) -> None:
        self.kills[(shooter_kind, target_kind)] += 1
        if weapon:
            self.weapon_kills[(team, shooter_kind, weapon, target_kind)] += 1

    def finish(self, elapsed: float, winner: int | None) -> None:
        self.elapsed = elapsed
        self.winner = winner

    def city_result(
        self,
        defending_team: int,
        buildings_alive: int,
        buildings_total: int,
        civilians_alive: int,
        civilians_total: int,
        cars_alive: int,
        cars_total: int,
    ) -> None:
        self.city = {
            "defending_team": TEAM_LABELS[defending_team],
            "buildings_alive": buildings_alive,
            "buildings_total": buildings_total,
            "civilians_alive": civilians_alive,
            "civilians_total": civilians_total,
            "cars_alive": cars_alive,
            "cars_total": cars_total,
        }

    # ------------------------------------------------------------------
    # Reporting
    # ------------------------------------------------------------------
    def _kinds_seen(self) -> list[str]:
        seen = {kind for _, kind in self.deployed}
        seen |= {a for a, _ in self.hits} | {b for _, b in self.hits}
        return [k for k in KIND_LABELS if k in seen]

    def render(self) -> str:
        lines: list[str] = []
        add = lines.append

        add("=" * 74)
        add("  INFORME DE BATALLA")
        add("=" * 74)
        add(f"  semilla        {self.seed}")
        add(f"  comenzada      {self.started:%Y-%m-%d %H:%M:%S}")
        add(f"  duracion       {self.elapsed:.1f} s simulados")
        outcome = {0: "gana Rojo", 1: "gana Azul", -1: "aniquilacion mutua"}.get(
            self.winner, "sin resolver"
        )
        add(f"  resultado      {outcome}")
        add("")

        if self.city is not None:
            add("-" * 74)
            add("  OBJETIVO CIUDAD")
            add("-" * 74)
            add(f"  equipo defensor  {self.city['defending_team']}")
            add(
                f"  edificios        {self.city['buildings_alive']} / "
                f"{self.city['buildings_total']} en pie"
            )
            add(
                f"  civiles          {self.city['civilians_alive']} / "
                f"{self.city['civilians_total']} a salvo"
            )
            add(
                f"  coches civiles   {self.city['cars_alive']} / "
                f"{self.city['cars_total']} operativos"
            )
            add("")

        add("-" * 74)
        add("  FUERZAS DESPLEGADAS")
        add("-" * 74)
        kinds = self._kinds_seen()
        add(f"  {'unidad':22}{'Rojo':>8}{'Azul':>8}")
        for kind in kinds:
            add(
                f"  {KIND_LABELS[kind]:22}"
                f"{self.deployed[(0, kind)]:>8}{self.deployed[(1, kind)]:>8}"
            )
        add("")

        add("-" * 74)
        add("  DISPAROS Y MUNICION")
        add("-" * 74)
        add(f"  {'unidad':22}{'disparos R':>12}{'disparos A':>12}")
        for kind in kinds:
            red, blue = self.shots[(0, kind)], self.shots[(1, kind)]
            if red or blue:
                add(f"  {KIND_LABELS[kind]:22}{red:>12}{blue:>12}")
        add("")
        if self.missiles:
            add(f"  {'misiles guiados':22}{'lanzados':>12}")
            by_weapon: collections.Counter = collections.Counter()
            for (_, _, weapon), count in self.missiles.items():
                by_weapon[weapon] += count
            for weapon, count in by_weapon.most_common():
                add(f"  {weapon:22}{count:>12}")
            add("")
        if self.intercepts:
            add(f"  {'intercepciones CIWS':22}{'destruidos':>12}")
            for (team, kind, weapon), count in sorted(self.intercepts.items()):
                description = f"{TEAM_LABELS[team]} {KIND_LABELS.get(kind, kind)} / {weapon}"
                add(f"  {description:40}{count:>8}")
            add("")
        if self.salvos:
            total = sum(self.salvos.values())
            add(f"  salvas estrategicas de submarino: {total} misiles de crucero")
            for (team, kind), count in sorted(self.salvos.items()):
                add(f"    {TEAM_LABELS[team]:5} {KIND_LABELS.get(kind, kind):22}{count:>6}")
            add("")

        add("-" * 74)
        add("  PRECISION  (impactos sobre disparos, por tipo de atacante)")
        add("-" * 74)
        add(f"  {'unidad':22}{'disparos':>10}{'impactos':>10}{'acierto':>10}{'dano':>12}")
        for kind in kinds:
            fired = self.shots[(0, kind)] + self.shots[(1, kind)]
            landed = sum(n for (a, _), n in self.hits.items() if a == kind)
            dealt = sum(d for (a, _), d in self.damage.items() if a == kind)
            if not fired and not landed:
                continue
            rate = f"{100.0 * landed / fired:.0f}%" if fired else "-"
            add(f"  {KIND_LABELS[kind]:22}{fired:>10}{landed:>10}{rate:>10}{dealt:>12.0f}")
        add("")

        add("-" * 74)
        add("  QUIEN DESTRUYO A QUIEN")
        add("-" * 74)
        if not self.kills:
            add("  (ninguna baja)")
        else:
            width = max(len(KIND_LABELS[k]) for k in kinds) + 2
            for (shooter, victim), count in sorted(
                self.kills.items(), key=lambda kv: (-kv[1], kv[0])
            ):
                arrow = f"{KIND_LABELS.get(shooter, shooter):{width}}-> {KIND_LABELS.get(victim, victim)}"
                add(f"  {arrow:<52}{count:>4}")
            add("")
            add(f"  {'unidad':22}{'bajas causadas':>16}{'perdidas':>12}")
            for kind in kinds:
                caused = sum(n for (a, _), n in self.kills.items() if a == kind)
                lost = sum(n for (_, b), n in self.kills.items() if b == kind)
                if caused or lost:
                    add(f"  {KIND_LABELS[kind]:22}{caused:>16}{lost:>12}")
        if self.weapon_kills:
            add("")
            add("  BAJAS ATRIBUIDAS POR ARMA")
            for (team, shooter, weapon, victim), count in sorted(
                self.weapon_kills.items(),
                key=lambda item: (-item[1], item[0]),
            ):
                description = (
                    f"{TEAM_LABELS[team]} - {KIND_LABELS.get(shooter, shooter)} / "
                    f"{weapon} -> {KIND_LABELS.get(victim, victim)}"
                )
                add(f"  {description:<66}{count:>4}")
        add("")
        add("=" * 74)
        return "\n".join(lines)

    def as_dict(self) -> dict:
        """Serializable form of the complete report, without tuple keys."""
        winner = {
            0: "Rojo",
            1: "Azul",
            -1: "aniquilacion mutua",
        }.get(self.winner, "sin resolver")
        return {
            "metadata": {
                "seed": self.seed,
                "started": self.started.isoformat(timespec="seconds"),
                "duration_seconds": self.elapsed,
                "winner": winner,
            },
            "city": self.city,
            "deployed": [
                {
                    "team": TEAM_LABELS[team],
                    "unit": kind,
                    "unit_label": KIND_LABELS.get(kind, kind),
                    "count": count,
                }
                for (team, kind), count in sorted(self.deployed.items())
            ],
            "shots": [
                {
                    "team": TEAM_LABELS[team],
                    "unit": kind,
                    "count": count,
                }
                for (team, kind), count in sorted(self.shots.items())
            ],
            "guided_weapons": [
                {
                    "team": TEAM_LABELS[team],
                    "unit": kind,
                    "weapon": weapon,
                    "launched": count,
                }
                for (team, kind, weapon), count in sorted(self.missiles.items())
            ],
            "missile_intercepts": [
                {
                    "team": TEAM_LABELS[team],
                    "unit": kind,
                    "system": weapon,
                    "destroyed": count,
                }
                for (team, kind, weapon), count in sorted(self.intercepts.items())
            ],
            "strategic_salvos": [
                {
                    "team": TEAM_LABELS[team],
                    "unit": kind,
                    "missiles_launched": count,
                }
                for (team, kind), count in sorted(self.salvos.items())
            ],
            "hits_and_damage": [
                {
                    "attacker": attacker,
                    "target": target,
                    "hits": count,
                    "damage": self.damage[(attacker, target)],
                }
                for (attacker, target), count in sorted(self.hits.items())
            ],
            "kills": [
                {
                    "attacker": attacker,
                    "target": target,
                    "count": count,
                }
                for (attacker, target), count in sorted(self.kills.items())
            ],
            "weapon_kills": [
                {
                    "team": TEAM_LABELS[team],
                    "attacker": attacker,
                    "weapon": weapon,
                    "target": target,
                    "count": count,
                }
                for (team, attacker, weapon, target), count in sorted(
                    self.weapon_kills.items()
                )
            ],
        }

    def write(self, directory: str | Path = ".") -> tuple[Path, Path]:
        """Write matching human-readable and machine-readable reports."""
        root = Path(directory)
        txt_dir = root / "txt"
        json_dir = root / "json"
        txt_dir.mkdir(parents=True, exist_ok=True)
        json_dir.mkdir(parents=True, exist_ok=True)

        stem = f"batalla_{self.started:%Y%m%d_%H%M%S}_semilla{self.seed}"
        txt_path = txt_dir / f"{stem}.txt"
        json_path = json_dir / f"{stem}.json"
        txt_path.write_text(self.render(), encoding="utf-8")
        json_path.write_text(
            json.dumps(self.as_dict(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return txt_path, json_path
