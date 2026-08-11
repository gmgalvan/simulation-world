# Condiciones de victoria y defensa de la ciudad

Este documento describe cuándo cambia de fase y cuándo termina una batalla con
la ciudad activada mediante `--city=true`.

## Equipos y objetivo urbano

Al generar la batalla, la semilla asigna la ciudad al equipo Rojo o Azul. Ese
equipo es el **defensor** y el contrario es el **atacante** de la ciudad. La
asignación es determinista: una misma semilla produce el mismo defensor.

La ciudad comienza con:

- 8 edificios;
- 18 personas civiles;
- 6 vehículos civiles.

El HUD utiliza `E` para edificios, `P` para personas y `V` para vehículos. Por
ejemplo, `E:8/8 P:18/18 V:6/6` indica que no ha habido pérdidas urbanas.
Debajo de las dos listas militares aparece una tercera línea del color del
defensor con el detalle civil: supervivientes y bajas de personas, coches
operativos y destruidos, y edificios en pie y caídos.

## Fase 1: combate militar

Mientras sobreviva al menos una unidad militar enemiga, la IA solo puede
seleccionar unidades militares como blancos. Edificios, personas y coches no
entran en la selección, aunque estén más cerca.

Esta regla considera militares de tierra, aire, superficie y submarinos. Un
submarino enemigo sumergido también mantiene activa la fase militar aunque una
unidad concreta no pueda detectarlo. Los daños civiles todavía pueden suceder
de manera indirecta por el área de una explosión dirigida contra un blanco
militar.

Los submarinos no reservan misiles para la ciudad durante esta fase: sus salvas
estratégicas se dirigen a fuerzas militares enemigas.

## Fase 2: combate urbano

Si el defensor pierde todas sus unidades militares y todavía queda al menos
una unidad atacante, la batalla no termina inmediatamente. Comienza la fase
urbana y edificios, coches y personas civiles expuestas pasan a ser blancos
seleccionables. Las personas que permanecen dentro de un refugio no pueden ser
seleccionadas directamente; pueden salir al evacuar un edificio crítico.
Entre los activos urbanos, la IA da prioridad a las personas que ya están
expuestas, luego a los vehículos y finalmente a los edificios. La distancia y
una pequeña variación determinista evitan que todo el ejército elija exactamente
el mismo blanco.

En esta fase los submarinos atacantes pueden dirigir sus salvas estratégicas
contra edificios de la ciudad.

## Derrota de la ciudad

La ciudad falla cuando se cumple cualquiera de estas condiciones:

- quedan 3 o menos de sus 8 edificios en pie; o
- quedan 9 o menos de sus 18 personas con vida.

En términos generales, la condición del código es edificios vivos menores o
iguales al 40% del total, o personas vivas menores o iguales al 50% del total.
Como los conteos son enteros, el límite efectivo de edificios es 3 de 8.

Los vehículos civiles aparecen en el HUD y en los informes TXT/JSON, pero su
destrucción no provoca por sí sola la derrota de la ciudad.

## Resolución de la partida

| Situación | Resultado |
|---|---|
| Ambos equipos conservan militares | Continúa la fase militar |
| El defensor queda sin militares y el atacante conserva alguno | Comienza la fase urbana |
| La ciudad alcanza uno de sus umbrales de derrota | Gana el atacante |
| El atacante queda sin militares y el defensor conserva alguno | Gana el defensor |
| Ambos ejércitos quedan sin militares y la ciudad todavía resiste | Gana el defensor |
| Ambos ejércitos quedan sin militares, pero la ciudad ya falló | Gana el atacante |

La comprobación de derrota de la ciudad se realiza antes que la comprobación
de ejércitos restantes. Esto permite que un último impacto válido decida la
partida aunque la unidad que lo lanzó también sea destruida.

## Partidas sin ciudad

Con `--city=false` no existen fases ni objetivo urbano. La partida termina
cuando queda un solo equipo con unidades militares. Si ambos pierden todas sus
unidades, el resultado es aniquilación mutua.

## Estadísticas

Los informes guardan el defensor asignado, edificios y personas supervivientes,
coches civiles operativos, impactos, daño y bajas atribuidas al tipo de atacante
y al arma cuando se conoce. Los archivos se escriben por defecto en:

- `informes/txt/`;
- `informes/json/`.

La implementación principal de estas reglas se encuentra en
`src/simulation_world/battle.py`; el estado y los umbrales urbanos están en
`src/simulation_world/city.py`.
