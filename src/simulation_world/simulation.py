"""Command line entry point."""

from __future__ import annotations

import argparse
from pathlib import Path


def _bool_flag(value: str) -> bool:
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "si", "sí", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise argparse.ArgumentTypeError("usa true/false, yes/no, si/no o 1/0")


def build_argparser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="simulation-world",
        description=(
            "Batalla 3D autónoma en un mundo abierto infinito: infantería, tanques, "
            "helicópteros y convertiplanos, con física rígida Bullet y render en Panda3D."
        ),
    )
    parser.add_argument("--seed", type=int, default=0, help="Semilla del terreno y del despliegue.")
    parser.add_argument(
        "--escenario",
        type=Path,
        default=None,
        metavar="ARCHIVO.yaml",
        help=(
            "Orden de batalla en YAML, con secciones 'rojo' y 'azul'. Permite bandos "
            "asimétricos, cosa que las opciones --n-* no pueden: esas dan la misma "
            "cantidad a los dos equipos. Si se pasa, manda sobre las --n-*."
        ),
    )
    parser.add_argument(
        "--city",
        type=_bool_flag,
        default=True,
        metavar="BOOL",
        help="Activa la ciudad defendida (true por defecto; usa --city=false para quitarla).",
    )
    parser.add_argument("--n-heli", type=int, default=4, help="Helicópteros por equipo.")
    parser.add_argument("--n-tanks", type=int, default=6, help="Tanques por equipo.")
    parser.add_argument(
        "--n-submarines",
        type=int,
        default=1,
        help="Submarinos lanzamisiles por equipo. Sumergidos: solo los ven aeronaves y otros submarinos.",
    )
    parser.add_argument(
        "--n-destroyers",
        type=int,
        default=2,
        help="Destructores lanzamisiles por equipo. Operan desde el agua contra aire y tierra.",
    )
    parser.add_argument(
        "--n-jets",
        type=int,
        default=8,
        help="Cazas F-35 por equipo. No pueden quedarse quietos: hacen pasadas de ataque.",
    )
    parser.add_argument(
        "--n-sam",
        type=int,
        default=2,
        help="Baterías antiaéreas por equipo. Superan en alcance a los cazas; contra tierra casi no hacen nada.",
    )
    parser.add_argument(
        "--n-rifles", type=int, default=16, help="Fusileros (AK-47) por equipo."
    )
    parser.add_argument(
        "--n-rockets",
        type=int,
        default=5,
        help="Equipos de RPG por equipo. Pocos, pero son lo único a pie que revienta un tanque.",
    )
    parser.add_argument(
        "--n-osprey",
        type=int,
        default=2,
        help="Convertiplanos (tiltrotor tipo V-22) por equipo. Rápidos y duros, poco armados.",
    )
    parser.add_argument("--relief", type=float, default=46.0, help="Altura máxima del relieve.")
    parser.add_argument(
        "--feature-scale",
        type=float,
        default=220.0,
        help="Escala de los accidentes del terreno en metros. Más alto = valles y sierras más amplios.",
    )
    parser.add_argument(
        "--clear-radius",
        type=float,
        default=420.0,
        metavar="M",
        help="Radio sin ríos ni lagos alrededor del campo de batalla (0 peara permitirlos en medio).",
    )
    parser.add_argument(
        "--view-chunks",
        type=int,
        default=5,
        help="Radio de terreno cargado, en chunks. Más alto = ves más lejos y cuesta más.",
    )
    parser.add_argument(
        "--chunk-size", type=float, default=128.0, help="Lado de cada chunk de terreno en metros."
    )
    parser.add_argument(
        "--trees", type=int, default=65, help="Árboles orgánicos por chunk (0 para ninguno)."
    )
    parser.add_argument(
        "--deploy",
        type=float,
        default=240.0,
        help="Separación inicial entre los dos bandos, en metros.",
    )
    parser.add_argument(
        "--stats-dir",
        default="informes",
        metavar="DIR",
        help="Carpeta base para informes; crea subcarpetas txt/ y json/.",
    )
    parser.add_argument(
        "--assets",
        metavar="DIR",
        help="Carpeta de assets (por defecto ./assets). Los modelos van en <DIR>/models/.",
    )
    parser.add_argument(
        "--resolution",
        default="1600x900",
        metavar="ANCHOxALTO",
        help="Tamaño de la ventana o de las capturas.",
    )
    parser.add_argument(
        "--shots",
        type=int,
        metavar="N",
        help="Modo sin ventana: simula y guarda N capturas PNG. Útil sin entorno gráfico.",
    )
    parser.add_argument(
        "--shots-dir", default="shots", metavar="DIR", help="Carpeta destino de las capturas."
    )
    parser.add_argument(
        "--shot-interval",
        type=float,
        default=1.5,
        help="Segundos simulados entre capturas en modo --shots.",
    )
    return parser


def _configure_panda(args) -> None:
    from panda3d.core import loadPrcFileData

    try:
        width, height = (int(v) for v in args.resolution.lower().split("x"))
    except ValueError:
        width, height = 1600, 900

    settings = [
        f"win-size {width} {height}",
        "window-title Simulation World - helicopteros vs tanques",
        # No sound card under WSL, and no /dev/input either; silence both.
        "audio-library-name null",
        "notify-level-device fatal",
        "framebuffer-multisample 1",
        "multisamples 4",
        "textures-power-2 none",
    ]
    if args.shots:
        settings.extend(("window-type offscreen", "load-display p3headlessgl"))
    loadPrcFileData("simulation-world", "\n".join(settings))


def _render_shots(app, args) -> None:
    """Step the simulation with a fixed clock and dump PNG frames."""
    from panda3d.core import ClockObject, Filename

    out_dir = Path(args.shots_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    clock = app.clock
    clock.set_mode(ClockObject.M_non_real_time)
    clock.set_dt(1.0 / 60.0)

    steps_between = max(1, int(args.shot_interval * 60))
    for index in range(args.shots):
        for _ in range(steps_between):
            app.task_mgr.step()
        path = out_dir / f"shot_{index:03d}.png"
        app.win.save_screenshot(Filename.from_os_specific(str(path)))
        print(f"[shot] {path}  t={app.sim_time:5.1f}s  {app.battle.status_text()}")


def resolve_roster(args) -> None:
    """Settle the order of battle onto `args`, from the file or the options.

    The file wins where it speaks, so a scenario is reproducible on its own,
    and the --n-* options stay as the quick symmetric skirmish.
    """
    from .scenario import ScenarioError, load, symmetric_roster

    counts = {
        "helicopter": args.n_heli,
        "tank": args.n_tanks,
        "osprey": args.n_osprey,
        "jet": args.n_jets,
        "sam": args.n_sam,
        "rifleman": args.n_rifles,
        "rocket": args.n_rockets,
        "destroyer": args.n_destroyers,
        "submarine": args.n_submarines,
    }
    args.city_team = None
    if args.escenario is None:
        args.roster = symmetric_roster(counts)
        return

    try:
        scenario = load(args.escenario)
    except ScenarioError as error:
        raise SystemExit(f"[escenario] {error}")
    args.roster = scenario.roster
    if scenario.seed is not None:
        args.seed = scenario.seed
    if scenario.city is not None:
        args.city = scenario.city
    args.city_team = scenario.city_team
    print(scenario.summary())


def main(argv: list[str] | None = None) -> None:
    args = build_argparser().parse_args(argv)
    resolve_roster(args)
    _configure_panda(args)

    from .app import SimulationApp

    app = SimulationApp(args)
    if args.shots:
        _render_shots(app, args)
    else:
        app.run()


if __name__ == "__main__":
    main()
