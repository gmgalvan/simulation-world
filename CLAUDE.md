# CLAUDE.md

Las instrucciones de este repositorio viven en **[AGENTS.md](AGENTS.md)**. Léelo
antes de tocar nada.

Se mantiene en un solo archivo a propósito: dos listas de convenciones acaban
divergiendo, y entonces ninguna de las dos es de fiar.

## Lo mínimo que hay que saber

```bash
uv sync                                   # instalar
uv run main.py                            # ejecutar
uv run main.py --help                     # opciones
uv run python -m compileall -q main.py src   # comprobación sintáctica
```

Sin entorno gráfico, la única forma de ver el resultado es renderizar sin
ventana y **abrir la imagen**:

```bash
uv run main.py --shots 2 --shot-interval 6 --shots-dir /tmp/shots --seed 4
```

## Dos cosas que este proyecto castiga

**Leer el código no basta.** El comportamiento es emergente y estocástico. Casi
todos los fallos reales aquí —proyectiles que rebotaban, misiles que no
alcanzaban a nada, unidades atascadas, aviones que se suicidaban contra la
flota— aparecieron al **medir**, no al revisar. Antes de dar por bueno un cambio
de balance, IA o física, mide el antes y el después.

**Un test que solo da resultados negativos no prueba nada.** Incluye siempre un
caso de control que deba salir positivo. Una tanda entera dio "sin daño" en este
repositorio porque el lanzador estaba colocado bajo tierra: los tres casos
pasaban por trivialidad.

`AGENTS.md` tiene la lista de trampas concretas ya pisadas (nodos huérfanos,
`__slots__`, palas de rotor, amortiguación compuesta y demás). Vale la pena
mirarla antes de depurar algo raro.
