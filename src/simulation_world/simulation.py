"""Command line entry point."""

from __future__ import annotations

import argparse
from pathlib import Path


def build_argparser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="simulation-world",
        description=(
            "Batalla 3D autónoma en un mundo abierto infinito: infantería, tanques, "
            "helicópteros y convertiplanos, con física rígida Bullet y render en Panda3D."
        ),
    )
    parser.add_argument("--seed", type=int, default=0, help="Semilla del terreno y del despliegue.")
    parser.add_argument("--n-heli", type=int, default=3, help="Helicópteros por equipo.")
    parser.add_argument("--n-tanks", type=int, default=4, help="Tanques por equipo.")
    parser.add_argument(
        "--n-submarines",
        type=int,
        default=1,
        help="Submarinos lanzamisiles por equipo. Sumergidos: solo los ven aeronaves y otros submarinos.",
    )
    parser.add_argument(
        "--n-destroyers",
        type=int,
        default=1,
        help="Destructores lanzamisiles por equipo. Operan desde el agua contra aire y tierra.",
    )
    parser.add_argument(
        "--n-jets",
        type=int,
        default=4,
        help="Cazas F-35 por equipo. No pueden quedarse quietos: hacen pasadas de ataque.",
    )
    parser.add_argument(
        "--n-sam",
        type=int,
        default=2,
        help="Baterías antiaéreas por equipo. Superan en alcance a los cazas; contra tierra casi no hacen nada.",
    )
    parser.add_argument(
        "--n-rifles", type=int, default=6, help="Fusileros (AK-47) por equipo."
    )
    parser.add_argument(
        "--n-rockets",
        type=int,
        default=3,
        help="Equipos de RPG por equipo. Pocos, pero son lo único a pie que revienta un tanque.",
    )
    parser.add_argument(
        "--n-osprey",
        type=int,
        default=1,
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
        help="Radio sin ríos ni lagos alrededor del campo de batalla (0 para permitirlos en medio).",
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
        "--trees", type=int, default=110, help="Árboles por chunk (0 para ninguno)."
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
        help="Carpeta donde se guarda el informe .txt al terminar la batalla.",
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
        settings.append("window-type offscreen")
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


def main(argv: list[str] | None = None) -> None:
    args = build_argparser().parse_args(argv)
    _configure_panda(args)

    from .app import SimulationApp

    app = SimulationApp(args)
    if args.shots:
        _render_shots(app, args)
    else:
        app.run()


if __name__ == "__main__":
    main()
