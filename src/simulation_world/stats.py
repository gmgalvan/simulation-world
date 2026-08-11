"""Battle bookkeeping: who shot what, what connected, and who killed whom.

Kept out of ``battle`` on purpose. The battle module is already the busiest
file in the project, and counting things has nothing to do with running a
simulation — it only ever observes.
"""

from __future__ import annotations

import collections
import datetime
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
        self.salvos: collections.Counter = collections.Counter()
        self.missiles: collections.Counter = collections.Counter()
        self.deployed: collections.Counter = collections.Counter()
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

    def hit(self, shooter_kind: str, target_kind: str, team: int, amount: float) -> None:
        self.hits[(shooter_kind, target_kind)] += 1
        self.damage[(shooter_kind, target_kind)] += amount

    def kill(self, shooter_kind: str, target_kind: str, team: int) -> None:
        self.kills[(shooter_kind, target_kind)] += 1

    def finish(self, elapsed: float, winner: int | None) -> None:
        self.elapsed = elapsed
        self.winner = winner

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
        add("")
        add("=" * 74)
        return "\n".join(lines)

    def write(self, directory: str | Path = ".") -> Path:
        path = Path(directory) / f"batalla_{self.started:%Y%m%d_%H%M%S}_semilla{self.seed}.txt"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.render(), encoding="utf-8")
        return path
