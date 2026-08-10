# AGENTS.md

## Alcance

Estas instrucciones aplican a todo el repositorio. Mantén los cambios acotados a la tarea y conserva cualquier modificación no relacionada que ya exista en el árbol de trabajo.

## Proyecto

`simulation-world` es una simulación 3D autónoma escrita para Python 3.11 con Panda3D, Bullet y NumPy. Usa un layout `src/`; `main.py` y el script `simulation-world` delegan en `simulation_world.simulation:main`.

El código se divide por responsabilidades:

- `terrain.py`, `chunks.py` y `scenery.py`: generación procedural y streaming del mundo.
- `entities.py`, `battle.py` y `missiles.py`: unidades, IA, combate y guiado.
- `effects.py` y `assets.py`: efectos visuales, modelos externos y placeholders.
- `app.py` y `simulation.py`: aplicación Panda3D, cámara, HUD y CLI.
- `assets/models.json`: metadatos de escala, orientación y partes animadas de modelos.

Consulta `README.md` para el comportamiento esperado y `assets/README.md` antes de cambiar la carga o convención de modelos.

## Entorno y comandos

- Instala las dependencias con `uv sync`.
- Ejecuta la aplicación con `uv run main.py`.
- Consulta las opciones con `uv run main.py --help`.
- Haz una comprobación sintáctica con `uv run python -m compileall -q main.py src`.
- Para una prueba visual reproducible sin ventana, usa una semilla fija y una salida temporal, por ejemplo:
  `uv run main.py --shots 1 --shot-interval 0.1 --shots-dir /tmp/simulation-world-shots --seed 0`.

Actualmente no hay una suite de tests automatizados. Para cambios de lógica, añade tests deterministas cuando sea práctico; para cambios de render, terreno o física, ejecuta al menos la comprobación sintáctica y una captura headless.

## Convenciones de desarrollo

- Conserva la compatibilidad con Python 3.11 y las anotaciones de tipos existentes.
- Mantén `from __future__ import annotations` en los módulos que ya lo usan.
- Prefiere funciones pequeñas y datos deterministas. El terreno, la vegetación y el despliegue deben reproducirse con la misma semilla.
- No introduzcas estado global aleatorio ni uses `hash()` para semillas persistentes; su resultado puede cambiar entre procesos.
- Respeta las unidades existentes: posiciones y distancias en metros, tiempo en segundos y ángulos según la API concreta de Panda3D.
- No mezcles los sistemas de coordenadas de Panda3D/NodePath con arrays de NumPy sin hacer explícita la conversión.
- Mantén sincronizadas la geometría visible y la geometría de colisión cuando cambies el terreno.
- Los proyectiles usan barridos por rayo y los vehículos usan Bullet; no conviertas proyectiles en cuerpos rígidos sin revisar filtrado, rebotes y tunneling.
- Evita asignaciones por frame en rutas sensibles. Conserva la creación vectorizada de mallas y el batching de vegetación.
- Añade una opción de CLI para cualquier parámetro nuevo que el usuario deba poder ajustar y documenta el cambio en `README.md`.
- Los assets opcionales deben degradarse de forma segura a los placeholders procedurales.

## Verificación

Antes de terminar:

1. Ejecuta la comprobación más pequeña relacionada con el cambio.
2. Si tocaste simulación, física, terreno, cámara, efectos o assets, genera al menos una captura headless con semilla fija.
3. Revisa `git diff --check` y confirma que no se hayan añadido capturas, cachés, entornos virtuales ni modelos grandes por accidente.
4. Resume qué verificaste y cualquier limitación pendiente.

No regeneres ni elimines `uv.lock` salvo que cambien las dependencias. Si cambian, actualízalo con `uv` y conserva ambos archivos (`pyproject.toml` y `uv.lock`) en sincronía.
