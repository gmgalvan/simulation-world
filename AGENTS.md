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
- `stats.py`: recuento de disparos, impactos y bajas, e informe `.txt` final. Solo observa: nunca debe influir en la simulación.
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
- Todo tipo de unidad nuevo debe entrar en `SPECS` y en los conjuntos que le correspondan (`GROUND`, `FLYING`, `NAVAL`, `INFANTRY`, `SUBSURFACE`…), y tener despacho de controlador en `Battle.step`. Un tipo sin conjunto acaba usando el controlador equivocado en silencio.

## Trampas conocidas

Errores que ya se han cometido en este repositorio. Merece la pena comprobarlos antes de dar algo por terminado.

- **Nodos huérfanos.** `make_box` y `make_loft` devuelven un `NodePath` suelto; si olvidas `reparent_to`, la pieza no aparece y no hay error. Ha pasado con las tomas del caza, las chimeneas del destructor y los esponjones del Osprey.
- **Palas de rotor.** Una caja centrada cuenta como **dos** palas. Un rotor tripala necesita cajas desplazadas a un lado repartidas en 360°, o salen seis.
- **`__slots__`.** Varias clases lo usan (`Missile`, `Shell`). Añadir un atributo en `__init__` sin declararlo en `__slots__` revienta en tiempo de ejecución, no al importar.
- **Modos de cámara.** `camera_mode` admite valores fuera de `CAMERA_MODES` (por ejemplo `"inspect"`). Cualquier código que haga `CAMERA_MODES.index(...)` debe tolerarlo.
- **Sin ventana no hay ratón.** `mouseWatcherNode` es `None` en modo `--shots`; protégelo antes de usarlo.
- **Nombres de scripts de prueba.** No llames a un script `inspect.py`, `types.py`, etc.: eclipsan módulos de la biblioteca estándar y provocan errores de importación desconcertantes.
- **Amortiguación compuesta.** Un coeficiente que parece pequeño aplicado por frame se compone: `speed *= (1 - 0.16*dt)` a 120 Hz deja un misil por debajo de la velocidad de su blanco en pocos segundos.
- **Rozamiento contra empuje.** La fuerza de avance se equilibra con el rozamiento en `cruise_speed - mu*g/drive_gain`. Una ganancia baja limita la unidad muy por debajo de su `cruise_speed` nominal sin que nada lo indique.

## Cómo verificar cambios de comportamiento

La simulación es estocástica y emergente: leer el código no basta para saber si un cambio hizo lo que pretendía.

- **Mide antes y después.** Casi todos los bugs de esta simulación se han encontrado midiendo (tasa de impacto, tiempo atascado, daño por tipo), no leyendo.
- **Incluye siempre un caso de control** que *deba* dar resultado positivo. Una tanda donde todo da cero puede estar pasando por trivialidad: ocurrió con un test de misiles cuyo lanzador estaba bajo tierra.
- **Comprueba que las partidas terminan.** Varias veces una unidad nueva ha creado un empate infinito: unidades que no pueden dañarse entre sí, o flotas en mares separados. Prueba escenarios con un solo tipo de unidad superviviente.
- Los scripts de medición van en un directorio temporal, nunca en el repositorio.

## Verificación

Antes de terminar:

1. Ejecuta la comprobación más pequeña relacionada con el cambio.
2. Si tocaste simulación, física, terreno, cámara, efectos o assets, genera al menos una captura headless con semilla fija **y mírala**: muchos fallos de geometría solo se ven.
3. Si tocaste balance, IA o armamento, mide el efecto y compáralo con el estado anterior.
4. Revisa `git diff --check` y confirma que no se hayan añadido capturas, cachés, entornos virtuales ni modelos grandes por accidente.
5. Resume qué verificaste y cualquier limitación pendiente.

No regeneres ni elimines `uv.lock` salvo que cambien las dependencias. Si cambian, actualízalo con `uv` y conserva ambos archivos (`pyproject.toml` y `uv.lock`) en sincronía.
