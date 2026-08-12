# simulation-world 🚁🪖

Batalla 3D autónoma en un **mundo abierto infinito**, con **física de cuerpo
rígido real (Bullet)** y render en **Panda3D**.

Dos equipos (rojo y azul) se despliegan enfrentados, se buscan solos y combaten
hasta que uno queda en pie. **Nueve tipos de unidad**, con papeles que se
contrarrestan entre sí:

| | Unidad | Papel |
|---|---|---|
| 🚶 | Fusilero (AK-47) | Numeroso y barato. Inútil contra blindaje |
| 🎯 | Equipo de RPG | Anticarro. Hacen falta ~3 por tanque enemigo |
| 🛡️ | Tanque Leopard 2 | Para a disparar; un obús mata a un soldado de un tiro |
| 📡 | Batería antiaérea | La respuesta desde tierra al poder aéreo |
| 🚁 | Helicóptero Mi-24 | Anticarro volador |
| 🛩️ | Convertiplano V-22 | Rápido y duro, poco armado |
| ✈️ | Caza F-35 | Vuela alto y ataca con misiles guiados |
| 🚢 | Destructor | Misiles de 1200 m aire/tierra, y sonar antisubmarino |
| 🌊 | Submarino | Sumergido, torpedos y salva de crucero de alcance ilimitado |

## Qué hace

**Mundo infinito.** El terreno no tiene bordes: se genera por *chunks* según
hacen falta y se descarta lo que queda atrás. La clave es que la altura es una
**función pura de las coordenadas del mundo** — dos chunks vecinos evalúan la
misma función en su borde común, así que encajan sin grietas y sin llevar
ninguna contabilidad. Además permite consultar la altura en cualquier punto,
incluso de terreno no cargado, que es de lo que dependen la IA y la balística.

La geometría de cada chunk se construye **volcando un array de numpy directo al
buffer de vértices** (`v3n3c4` = 28 bytes por vértice) en vez de escribir
vértice a vértice: 2.4 ms por chunk en lugar de cientos. La *misma* malla se usa
para dibujar y para colisionar, así que visual y física no se pueden desalinear.

Se cargan dos radios distintos a propósito: todo lo que entra en el radio de
visión se dibuja, pero solo el radio interior recibe collider de Bullet —
construirlos es lo caro y solo las unidades y los proyectiles los necesitan.

**Relieve y agua.** El campo de batalla se mantiene **seco**: los ríos y lagos
se atenúan dentro de un radio alrededor del origen (`--clear-radius`, 420 m por
defecto) y aparecen a partir de ahí. Un río cruzando por en medio simplemente
ahoga a los terrestres, que no tienen más remedio que vadearlo. Medido: 0% de
agua hasta 300 m del centro, ~25% a 420 m.

Una máscara de baja frecuencia decide dónde el mundo es llanura y dónde cordillera, así que un mundo infinito tiene regiones en vez de
ruido uniforme. Los **ríos** salen del conjunto de nivel de un campo suave (esa
curva es justo la planta de un río) y se tallan hundiendo el terreno bajo la
línea de agua; los lagos, de un campo de cuencas. Reparto típico: ~10% agua,
50% llanura, 20% colinas, 18% montaña con picos nevados a ~90 m.

**Modelos.** Los placeholders no son solo cajas: hay un constructor de
**superficies lofteadas** (`make_loft`) que une secciones transversales, que es
como se modela bien en low-poly. Con cajas no se puede describir un ala en
flecha ni un fuselaje con *chines*; con secciones sí, y sigue siendo una cara
plana por cuadrilátero, así que el resultado no se sale del estilo facetado.

Con eso están hechos el caza (silueta de **F-35**: morro con chines, canopy,
tomas laterales, colas inclinadas), el tanque (**Leopard 2**: glacis inclinado,
torre en cuña, cañón de 120 mm con manguito térmico, faldones), el helicóptero
(**Mi-24**: doble burbuja en tándem, alas cortas con anhedro y pilones, torreta
bajo el morro, rotor de cinco palas) y el convertiplano (**V-22**: morro caído,
ala alta, góndolas de punta de ala con rotores tripala y cola en H con derivas
inclinadas).

Un detalle que muerde dos veces: una pala hecha con una caja centrada cuenta
como **dos** palas, así que un rotor tripala sale con seis. Las palas van
desplazadas a un lado y repartidas en 360°.

**Vegetación.** Bosque low-poly generado por chunk, sembrado desde una semilla
derivada de las coordenadas del chunk: un trozo de mundo **vuelve a crecer
exactamente igual** cada vez que se recarga. No crecen en el agua, en la playa
ni por encima de la cota de nieve. El bosque mezcla coníferas de copa escalonada
y ramas bajas visibles con árboles caducifolios de troncos ahusados, ramas y
ramitas bifurcadas, copas redondeadas irregulares y grupos de hojas puntiagudas;
ya no utiliza cubos para tronco y follaje. Todos los árboles de un chunk se funden en un
único `Geom` (una sola llamada de dibujado) y son **decorativos**: no entran al
mundo de Bullet, porque darles collider a cientos taparía los raycasts de línea
de visión por todas partes y atascaría la batalla.

**Física.** Bullet lleva gravedad, contactos, balística y escombros. Sobre eso:

- Los **helicópteros** no vuelan "por arte de magia": tienen un modelo de vuelo
  escrito a mano (controlador PD de altura que compensa el peso y corrige sobre
  el terreno) que se inyecta a Bullet **como fuerzas**. Un motor de cuerpo
  rígido no sabe nada de rotores — así se hace también en juegos reales.
  Operan a 38-54 m sobre el suelo, bastante por encima de las copas.
- Los **tanques** son cuerpos rígidos de 4.2 t que conducen por fuerzas.
- Los **proyectiles no están dentro del mundo físico**, y es a propósito: si un
  obús es un cuerpo rígido, el solver le resuelve el contacto y **rebota**
  visiblemente contra lo que debía destruir. Su movimiento es una integración de
  dos líneas (gravedad, o guiado propio en el caso del misil) y todos los
  impactos los encuentra un **rayo barrido** entre la posición anterior y la
  actual, que además evita que un obús rápido atraviese nada. Sacarlos del mundo
  físico subió los impactos de 20/30 a **30/30** en banco de pruebas.
  El barrido recorre *todos* los impactos y descarta el propio proyectil, a
  quien lo disparó y los escombros: quedarse con el más cercano sin filtrar hace
  que cada obús detone contra sí mismo en el primer frame.
- La IA de tiro resuelve el ángulo del obús (arco bajo) **anticipando** el
  movimiento del blanco. Un impacto de 120 mm **mata a un soldado de un tiro**,
  sea fusilero o equipo de RPG; los equipos antitanque sobreviven porque
  **superan en alcance al tanque** (112 m contra 78) y disparan primero, no
  porque aguanten un obús.
- Cada arma dispara como corresponde: los **tanques y los equipos de RPG paran
  para tirar** (`fire_halt`) y vuelven a moverse entre disparo y disparo — alto,
  fuego, avance —, mientras el **fusilero dispara en movimiento** y los
  helicópteros y convertiplanos siguen maniobrando en todo momento. El alto es
  bastante más corto que la recarga, así que avanzan igual: un tanque pasa ~10%
  del tiempo detenido, no clavado.
- Como aquí nada flota, los terrestres **sondean el terreno por delante** y
  esquivan tanto el agua como las **cuestas que no pueden subir** (más de 29°).
  Medido: antes esquivaban solo el agua y el 100% de los atascos eran contra
  una ladera. Encima llevan un **detector de atasco** — si quieren avanzar y
  llevan 1.5 s sin moverse, se desvían unos segundos — que cubre cualquier
  causa que el sondeo no vea. Atascos: 1.3% → **0%**.
- La **infantería** corre, se tumba con la pendiente y esquiva el agua igual
  que los tanques. Los **fusileros** son baratos y numerosos, con mucha cadencia
  y poco alcance; los **equipos de RPG** son pocos y lentos de recargar, pero
  disparan un cohete con estela de humo que sí revienta un blindado.
- Cada tipo tiene su propia **ganancia de empuje** (`drive_gain`). No es un
  detalle: la fuerza de avance se equilibra con el rozamiento del suelo en
  `cruise_speed − μg/ganancia`, así que una ganancia baja deja a la unidad muy
  por debajo de su velocidad nominal por mucho que se suba `cruise_speed`. Con
  la ganancia de los tanques heredada, los soldados corrían a 2.7 m/s de 7.
- El **caza** es el único que **no puede quedarse quieto**. Su controlador no es
  el de los rotores: el empuje va siempre hacia el morro, no hacia el objetivo,
  así que sobrepasa el blanco y tiene que dar la vuelta — de ahí salen las
  pasadas de ataque, que son emergentes y no una animación guionizada. Vuela muy
  alto y ataca con **misiles guiados**, no con cañón; solo dispara dentro de un
  cono de 30° en planta, alabea fuerte al virar y lleva postcombustión.
- La **batería antiaérea** es la respuesta desde tierra: alcance largo y letal
  contra aeronaves, pero contra objetivos terrestres apenas hace un 14-30% del
  daño. Ese resto **no es cero a propósito**: una unidad puramente antiaérea que
  no pueda dañar a nadie en tierra jamás termina una batalla, y dos baterías
  frente a frente se quedarían atascadas para siempre.
- El **convertiplano** bascula sus góndolas: horizontal (modo avión) para el
  traslado, donde vuela un 135% más rápido, y vertical al entrar en combate.
  Su capa base es de 58 m sobre el terreno, por encima de la formación normal
  de helicópteros (38, 46 y 54 m) y muy por debajo de los cazas (desde 135 m).
  El basculado es una animación real de las góndolas, no un cambio de textura.
  Ojo: el V-22 real es un **transporte**, no un cañonero, así que está
  modelado como rápido y resistente pero poco armado y menos ágil.
- Al morir, la unidad **suelta la restricción de rotación** y el pecio cae y
  da vueltas de verdad antes de reventar.

**IA.** Cada unidad elige blanco, se acerca, comprueba **línea de visión** con
un raycast y dispara. Mantiene el blanco hasta que muere o se aleja
(sin esa persistencia el fuego se concentra y la batalla se vuelve una paliza).
Si el terreno le tapa el tiro, cierra distancia en vez de orbitar una colina.
La puntería cae con la distancia. Cada tipo prioriza lo que puede matar de
verdad (tabla `PREFERENCE`): los tanques se buscan entre ellos en vez de
malgastar obuses contra helicópteros que orbitan, algo que un proyectil
balístico casi nunca alcanza, y todo el mundo trata de eliminar a los equipos
de RPG primero.

Además hay una tabla `DAMAGE_VS` de daño por tipo de blanco. Sin ella, un pelotón
de fusileros derriba tanques a base de volumen de fuego y llevar lanzacohetes
deja de tener sentido: un AK hace el **10%** de daño contra blindaje, mientras
que el RPG está hecho para eso.

**Misiles guiados.** Tienen su propio módulo y su propia clase, porque un misil
no es una piedra con campos extra: tiene motor con tiempo de combustión, buscador
con campo de visión, límite de maniobra del fuselaje, espoleta de proximidad y
una ley de guiado. La ley es **navegación proporcional**, la que usan los misiles
reales: en vez de apuntar al blanco (persecución pura, que se va a la cola y
pierde cualquier cosa rápida que cruce), ordena aceleración lateral proporcional
a la *velocidad de giro de la línea de visión*. Anular esa rotación es
exactamente la condición de colisión — si la demora deja de cambiar mientras la
distancia se acorta, vas a impactar — y por eso anticipa al blanco sin calcular
nunca un punto de intercepción.

Medido contra un caza en viraje sostenido, con el mismo presupuesto de maniobra:

| Límite de maniobra | Persecución pura | Navegación proporcional |
|---|---|---|
| 6 g | 0% | 19% |
| 8 g | 2.8% | 47% |
| 11 g (el que usa el juego) | 14% | **75%** |
| 18 g | 83% | 100% |

**Inspector de unidades.** Tecla `I`: la cámara se pega a una unidad concreta y
gira despacio a su alrededor para verla en detalle, con el encuadre ajustado al
tamaño de cada modelo — un fusilero y un caza llenan el plano por igual. `TAB` y
`SHIFT+TAB` recorren **todas** las unidades del campo, de cualquier bando,
incluidos los pecios. El marcador dice qué estás mirando y con cuánta vida.
Pulsa `V` sobre la unidad seleccionada para entrar en **vista de unidad**: la
cámara ocupa la posición de sus ojos, cabina, torreta o puente y apunta en la
misma dirección que su frente. En esa vista `TAB` y `SHIFT+TAB` cambian entre
unidades vivas; `V` vuelve al inspector y `I` regresa a la cámara de batalla.
La batería antiaérea usa la posición elevada de su estación óptica y sigue al
blanco fijado por el radar; cuando no tiene uno, explora ligeramente por encima
del horizonte.

**Control directo del fusilero.** Mientras inspeccionas un **fusilero** vivo o
ves desde sus ojos, pulsa `T` para tomarlo. La cámara pasa a primera persona:
`W/S` avanzan y retroceden, `A/D` giran y el clic izquierdo dispara por la mira
central. `T` devuelve la unidad a la IA. Esta opción no se ofrece al equipo RPG
ni a vehículos. Si el fusilero cae, el control se libera automáticamente; sus
disparos siguen usando la cadencia, precisión, daño y estadísticas normales.

**Control directo del caza.** Selecciona un **caza F-35** vivo y pulsa `T`.
`W/S` aumentan o reducen potencia, `A/D` hacen virar, `E` o `↑` ordenan ascenso
y `Q` o `↓` descenso. El HUD muestra la altura actual sobre el terreno. El
avión conserva una velocidad mínima, sustentación y protección
básica contra el terreno: no puede frenar en el aire ni comportarse como un
helicóptero. El clic izquierdo lanza su misil guiado únicamente si existe un
blanco enemigo dentro del alcance y del cono frontal; respeta la recarga y
queda registrado en las estadísticas normales. El recuadro de adquisición es
blanco sin bloqueo, amarillo durante la recarga y verde cuando el misil está
listo para lanzarse. En modo piloto aparece además un HUD propio con marcador
de vuelo, horizonte artificial, velocidad, altura, velocidad vertical, rumbo y
potencia; el recuadro sigue al blanco y pulsa cuando el misil está listo. Al
lanzar muestra `MISIL FUERA` y el proyectil deja un fogonazo y una nube inicial
más visibles. La vista incluye interior de cabina procedural: tablero, visera,
pantallas laterales, marco y montantes del parabrisas y cristal HUD translúcido.
El roster general se oculta durante el vuelo para despejar la vista. `T`
devuelve el caza a la IA.

**Cámara.** Se maneja con el ratón: **arrastra** para girar, **rueda** para
zoom. El teclado sigue funcionando, y en modo libre `WASD` mueve. Se usan
coordenadas absolutas de ratón en vez de captura relativa del puntero, que es
bastante más fiable entre sistemas de ventanas.

**Informe de batalla.** Al terminar, escribe el mismo informe en dos formatos:
`informes/txt/*.txt` para lectura y `informes/json/*.json` para análisis. Ambos
incluyen las fuerzas desplegadas, disparos y misiles por tipo, **precisión de cada unidad** y
la matriz completa de quién destruyó a quién. Las bajas causadas por armas
guiadas también indican el arma exacta y el equipo, por ejemplo
`Azul · submarino / torpedo -> destructor`; el daño radial conserva la
atribución al misil de crucero. Vive en su propio módulo
(`stats.py`) porque contar cosas no tiene nada que ver con simular: solo observa.

**HUD.** Marcador con las bajas de cada bando y una **leyenda por tipo de
unidad**, en el color del equipo, para leer de un vistazo qué le queda a cada
uno en vez de solo un total.

**Ciudad defendida.** Con `--city=true` (valor predeterminado), una ciudad
procedural aparece en un lateral del despliegue y queda bajo la protección de
un equipo elegido por la semilla. Tiene ocho edificios destructibles con
colisión, una red completa de calles continuas que siguen el relieve, seis
coches en circulación y dieciocho
civiles. Fachadas, coches, ropa y señalización heredan el rojo o azul del
equipo defensor. Coches y peatones usan colisionadores físicos y los carriles
opuestos están separados para evitar que el tráfico se atraviese. Al detectar
enemigos o una explosión, las personas que están fuera corren al edificio sano
más cercano. Si un refugio baja de 35% de integridad, sus ocupantes evacúan y
buscan otro; un edificio destruido cae, explota y deja escombros.

La arquitectura mezcla cuatro familias procedurales: torres contemporáneas de
ladrillo oscuro, zócalo de piedra, ventanas amplias con crucetas, marquesina,
parapeto y equipos de azotea;
rascacielos art déco con retranqueos, ventanas alineadas y agujas; y edificios
cívicos con cornisas, pórticos, columnas, tambor y cúpulas sólidas facetadas;
además de residenciales de uso mixto con ventanas individuales, balcones,
locales y cuartos de azotea. La piedra, metal, cristal y ladrillo usan paletas
arquitectónicas propias; el color del equipo queda limitado a toldos, placas y
banderas sujetas a los mástiles. Cada semilla varía
anchos, fondos, alturas y tonos sin perder la identificación del defensor.

Los civiles son personajes low-poly completos, con variantes masculinas y
femeninas, distintas estaturas, tonos de piel, cabello, ropa, rostro, brazos,
manos, piernas y calzado. Una prenda conserva el color del defensor para poder
identificarlos sin convertir el cuerpo entero en una ficha roja o azul. Al
correr muestran balanceo y desplazamiento vertical del cuerpo.

Los coches se generan como sedanes, hatchbacks y SUV de carrocería continua,
con cabina perfilada, parabrisas inclinado, cristales laterales, espejos, manijas,
faros, calaveras, defensas, placas y ruedas con neumático y rin. Las ruedas giran
al circular y la velocidad real del vehículo se utiliza para guiar proyectiles.

La selección de blancos ocurre en dos fases estrictas: mientras quede una sola
unidad militar enemiga, edificios, coches y civiles no pueden ser elegidos como
objetivos. Solo después de derrotar a todas las fuerzas defensoras comienza la
fase urbana y la batalla continúa sobre los activos civiles expuestos. Sus
impactos, bajas y el arma responsable quedan registrados en los informes TXT y
JSON como parte de las estadísticas.

El HUD muestra `E` (edificios), `P` (personas) y `V` (vehículos civiles). El defensor pierde
si queda en pie 40% o menos de los edificios o sobrevive 50% o menos de la
población. Los submarinos solo pueden dirigir sus salvas contra la ciudad
cuando ya no queda ninguna unidad militar enemiga.
Usa `--city=false` para jugar la batalla militar clásica sin ciudad.

La tabla completa de fases, umbrales y casos límite está en
[`docs/condiciones-de-victoria.md`](docs/condiciones-de-victoria.md).

**Guerra naval.** El mar corre a ambos lados del campo. El **destructor** lleva
tres escalones de armamento: CIWS/ametralladoras a menos de 82 m, cañón Mk 45
de 127 mm entre 82 y 310 m, y misiles guiados de hasta 1200 m contra aire y
tierra. Contra aeronaves, el lanzamiento naval tiene un ciclo nominal de 8 s
(con una variación de ±10%); el cañón, el CIWS y los ataques contra blancos de
superficie conservan sus propias cadencias. También posee sonar y puede atacar
submarinos.

Cuando empieza la fase urbana, los destructores atacantes se concentran en
**edificios** en lugar de perseguir peatones o coches. Lanzan un misil naval de
ataque terrestre con ascenso vertical, arco de 105 m y picado terminal, por lo
que una elevación entre la costa y la ciudad no exige línea de visión directa.
La cabeza causa 145 de daño, tiene 16 m de radio explosivo y una detonación
visual mayor que la del misil naval multipropósito.

El **CIWS también protege al propio destructor de misiles entrantes**. Vigila
los proyectiles guiados enemigos que lo tienen como blanco y abre fuego dentro
de 90 m en ráfagas de 0.25 s. Cada ráfaga tiene entre 22% y 48% de probabilidad
de destruir el misil según la cercanía; la intercepción produce trazadoras y
una detonación pequeña en vuelo. No puede detener torpedos bajo el agua. Las
intercepciones quedan registradas tanto en el informe TXT como en el JSON.

El **submarino** navega sumergido —91% del tiempo en las mediciones— y lleva
solo dos sistemas ofensivos: torpedos contra barcos y submarinos, y una salva
estratégica de hasta tres misiles contra objetivos lejanos. La primera salva se
prepara en 30 s y las siguientes cada 55 s. Para lanzarla emerge durante 7 s:
mientras está en superficie puede ser detectado por cualquier unidad.

Ambas flotas comparten **el mismo canal de mar**, separadas a lo largo de él, y
navegan al encuentro. Repartirlas entre los dos mares laterales con tierra en
medio hacía que no pudieran alcanzarse con nada salvo la salva, y una partida
que acababa solo con unidades navales **se quedaba atascada para siempre**.

Cuando está sumergido, el submarino **solo es visible para aeronaves, otros
submarinos y destructores**. Contra blancos a flote usa **torpedos**: lentos,
poco maniobreros, pero un impacto en el casco es casi decisivo y deja estela de
espuma. Contra tierra no tiene nada salvo la salva. Cada misil estratégico
inflige hasta 155 puntos en el centro y tiene un radio explosivo de 34 m con
caída de daño hasta 25% en el borde; la detonación grande arroja 18 fragmentos.

**Efectos.** Trazadoras, fogonazos, explosiones, escombros con física propia,
columnas de humo en unidades dañadas, barras de vida y sombras del sol.

La infantería **sangra en vez de explotar**: los impactos dejan salpicaduras y
la muerte una descarga de partículas rojas con arco balístico propio, calculado
a mano en lugar de con Bullet (una baja lanza una docena y no necesitan chocar
con nada). Van con blending normal, **no aditivo** como las explosiones — la
sangre no debe brillar. Los vehículos siguen ardiendo y humeando; los cuerpos
solo quedan tendidos.

Los soldados procedurales están **articulados**: cadera, rodillas, botas y
torso componen una marcha o carrera cuya cadencia sale de la velocidad real del
cuerpo. Si frenan para apuntar, trepan despacio o quedan atascados, sus pasos
también se frenan en vez de patinar sobre el suelo. Uniforme, casco, chaleco,
mochila y equipo tienen volumen propio; los brazos se resuelven desde hombro a
codo y de codo al punto exacto de agarre, así que el fusil y el RPG se sostienen
a dos manos incluso mientras corren.

**Cámara.** Cuatro modos (tecla `C`): órbita, persecución, cenital y **libre**.
Los automáticos enfocan la **pareja de enemigos más cercana** — el centroide de
todas las unidades cae en terreno vacío a medio camino entre los dos bandos y
deja el combate fuera de plano. Suavizan el paneo y abren el encuadre cuando esa
pareja está separada. La posición se calcula de forma exacta a partir del ángulo
deseado en lugar de interpolarse: al interpolarla, la distancia y el ángulo
reales nunca coinciden con los buscados y el plano acaba cenital.

En modo **libre** vuelas tú por el mundo y el terreno se va generando por
delante. La niebla está ajustada al radio de carga para que los chunks aparezcan
dentro de la bruma en vez de a la vista.

## Requisitos

- [`uv`](https://docs.astral.sh/uv/).
- OpenGL 3.2+. En WSL2 con WSLg funciona sin configurar nada (aquí corre
  acelerado por hardware vía D3D12 → Mesa). Sin entorno gráfico, usa `--shots`.

## Ejecutar

```bash
uv sync
uv run main.py
```

| Tecla | Acción |
|---|---|
| `I` | **Inspeccionar una unidad de cerca** (entrar/salir) |
| `TAB` / `SHIFT+TAB` | Unidad siguiente / anterior mientras inspeccionas |
| `V` | Ver de frente desde la unidad seleccionada / volver al inspector |
| `T` | Tomar / soltar el control del fusilero o caza seleccionado |
| `W/S`, `A/D`, clic izquierdo | Caminar, girar y disparar al controlar un fusilero |
| `W/S`, `A/D`, `Q/E`, clic izquierdo | Potencia, viraje, altura y misil del caza controlado |
| Arrastrar ratón | Girar la cámara |
| Rueda | Zoom (o avanzar, en modo libre) |
| `C` | Cambiar cámara: órbita → persecución → cenital → **libre** |
| `R` | Nueva batalla |
| `ESPACIO` | Pausa |
| `F` | Ver los colliders de Bullet (wireframe) |
| `ESC` | Salir |

Cámaras automáticas: `←` `→` giran, `↑` `↓` suben/bajan, `+` `-` hacen zoom.

**Cámara libre**: `WASD` moverse, `Q`/`E` bajar/subir, flechas mirar,
`SHIFT` para ir rápido, `M` activa el ratón (opcional: el modo relativo de ratón
no funciona en todos los entornos, por eso mirar con las flechas siempre va).

### Sin ventana (headless)

Simula y guarda capturas PNG. Útil sin entorno gráfico o para revisar una
batalla concreta:

```bash
uv run main.py --shots 8 --shot-interval 4 --shots-dir shots
```

### Opciones

```bash
uv run main.py --help
```

| Opción | Por defecto | Qué hace |
|---|---|---|
| `--seed N` | `0` | Semilla del mundo y del despliegue |
| `--city BOOL` | `true` | Ciudad defendida con edificios, coches y civiles (`--city=false` la desactiva) |
| `--n-heli N` | `3` | Helicópteros por equipo |
| `--n-tanks N` | `4` | Tanques por equipo |
| `--n-destroyers N` | `1` | Destructores por equipo. Misiles de 1200 m contra aire y tierra, y sonar para cazar submarinos |
| `--n-jets N` | `4` | Cazas F-35 por equipo |
| `--n-submarines N` | `1` | Submarinos por equipo. Sumergidos, con torpedos y la salva de crucero |
| `--n-sam N` | `2` | Baterías antiaéreas por equipo |
| `--n-rifles N` | `6` | Fusileros (AK-47) por equipo |
| `--n-rockets N` | `3` | Equipos de RPG por equipo. Hacen falta ~3 por tanque enemigo |
| `--n-osprey N` | `1` | Convertiplanos por equipo (`0` para ninguno) |
| `--relief N` | `46` | Altura máxima del relieve |
| `--clear-radius N` | `420` | Radio sin ríos alrededor del combate (`0` los permite en medio) |
| `--feature-scale N` | `220` | Escala de los accidentes. Más alto = valles y sierras más amplios |
| `--view-chunks N` | `5` | Radio de terreno cargado, en chunks. Más = ves más lejos y cuesta más |
| `--chunk-size N` | `128` | Lado de cada chunk en metros |
| `--trees N` | `65` | Árboles orgánicos **por chunk** (`0` para ninguno) |
| `--deploy N` | `240` | Separación inicial entre los dos bandos |
| `--resolution AxB` | `1600x900` | Tamaño de ventana o captura |
| `--assets DIR` | `./assets` | Carpeta de modelos |
| `--stats-dir DIR` | `informes` | Carpeta base; crea `txt/` y `json/` para cada formato |
| `--shots N` | — | Modo sin ventana: guarda N capturas PNG |
| `--shots-dir DIR` | `shots` | Carpeta de las capturas |
| `--shot-interval N` | `1.5` | Segundos simulados entre capturas |

Los destructores seleccionan su arma automáticamente: misil guiado para blancos a más de 310 m, cañón naval de 127 mm a distancia media y CIWS/ametralladoras contra amenazas cercanas (menos de 82 m).

El submarino prepara la primera salva estratégica en 30 s y después otra cada 55 s. Ataca hasta 3 objetivos en cualquier punto activo del mundo, sin límite táctico de alcance ni línea de visión. La trayectoria se calcula en `missiles.py`: lanzamiento vertical, arco parabólico guiado con 220 m de altura adicional nominal y picado terminal con navegación proporcional. Su explosión causa daño radial en 34 m. En modo inspección se muestra la cuenta regresiva.

Batalla grande y mucha distancia de visión:

```bash
uv run main.py --n-heli 6 --n-tanks 10 --n-osprey 2 --n-rifles 14 --n-rockets 5 \
  --view-chunks 7 --seed 12
```

Solo infantería, sin blindados ni aire:

```bash
uv run main.py --n-heli 0 --n-tanks 0 --n-osprey 0 --n-rifles 16 --n-rockets 3
```

Lomas suaves y pocos árboles (buen equilibrio para ver la batalla):

```bash
uv run main.py --relief 26 --feature-scale 150 --trees 22
```

Mundo pelado, sin árboles y casi llano:

```bash
uv run main.py --trees 0 --relief 18
```

Alta montaña nevada (queda espectacular, pero tapa mucho el combate):

```bash
uv run main.py --relief 62 --feature-scale 120 --trees 22
```

Sobre el relieve: los picos llegan a unas **2.1 veces** `--relief`, y la nieve
empieza a 52 m sobre el agua. Por eso `--relief 26` da lomas verdes y
`--relief 62` te llena el mundo de cumbres blancas. `--feature-scale` cambia el
*tamaño* de los accidentes, no su altura: bajarlo da montañitas frecuentes,
subirlo da valles y sierras más amplios.

## Modelos propios

Arranca sin ningún asset: si `assets/models/` está vacío, cada unidad se
construye con cajas procedurales. **Suelta un `.glb` o un `.obj` ahí y se usa
automáticamente** — se detecta por nombre, se escala solo al tamaño correcto y
se le aplica el color del equipo.

Guía completa (dónde descargar CC0, qué formato, cómo corregir orientación y
qué hace falta para que giren rotor y torreta): [`assets/README.md`](assets/README.md).

Al arrancar, la consola dice qué se cargó:

```
[assets] helicopter  -> helicopter.glb
[assets] tank        -> placeholder procedural
```

## Estructura

```
main.py                         # punto de entrada
assets/
  README.md                     # guía para meter modelos reales
  models.json                   # escala / orientación / nombres de piezas
  models/                       # tus .glb / .obj aquí
src/simulation_world/
  terrain.py                    # ruido infinito -> malla + collider por chunk
  chunks.py                     # streaming: carga/descarga alrededor de la accion
  scenery.py                    # bosque low-poly procedural (fundido en un Geom)
  city.py                       # ciudad, edificios, coches, civiles y refugios
  assets.py                     # carga de modelos y placeholders procedurales
                                #   (los nueve tipos, con geometría lofteada)
  missiles.py                   # misiles guiados: navegación proporcional
  entities.py                   # Unit: cuerpo rígido + modelo de vuelo/conducción
  effects.py                    # trazadoras, obuses, explosiones, escombros
  battle.py                     # IA de combate, disparo, bajas, victoria
  player_control.py             # control manual opcional de fusilero y caza
  flight_hud.py                 # instrumentos y adquisición del caza controlado
  app.py                        # ventana, luces, cámara, HUD, bucle principal
  stats.py                      # recuento e informes TXT/JSON de la batalla
  simulation.py                 # CLI
```

## Para agentes y colaboradores

Las convenciones del repositorio, las trampas ya pisadas y cómo verificar un
cambio están en [AGENTS.md](AGENTS.md). `CLAUDE.md` apunta ahí mismo: una sola
lista, para que no diverjan.
