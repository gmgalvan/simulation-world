# Cómo meter modelos reales

La simulación arranca sin ningún asset: si esta carpeta está vacía, cada unidad
se construye con cajas procedurales. En cuanto dejes un modelo aquí, se usa
automáticamente.

## 1. Dónde descargarlos

Recomendados por licencia **CC0** (dominio público, sin atribución obligatoria)
y por estilo low-poly coherente:

| Fuente | Qué buscar | Licencia |
|---|---|---|
| [kenney.nl/assets](https://kenney.nl/assets) | packs *Tanks*, *Vehicles* | CC0 |
| [quaternius.com](https://quaternius.com/) | *Military / Vehicles pack* | CC0 |
| [poly.pizza](https://poly.pizza/) | busca "helicopter", "tank" | CC0 / CC-BY |
| [sketchfab.com](https://sketchfab.com/search?type=models) | filtra **Downloadable** + licencia CC0/CC-BY | varía |

Si usas algo con licencia **CC-BY**, hay que dar crédito: apunta autor y enlace
en `assets/CREDITS.md`.

## 2. Formato

Panda3D lee aquí, por orden de preferencia:

1. **`.glb`** (glTF 2.0 binario) — el mejor: un solo archivo, texturas y
   materiales incluidos. Se carga con el plugin `panda3d-gltf`.
2. **`.gltf`** — igual, pero deja las texturas en archivos sueltos al lado.
3. **`.obj`** (+ `.mtl` + texturas) — el más universal, sin animación. Perfecto
   aquí, porque el rotor y la torreta los animo yo por código.
4. `.dae`, `.egg`, `.bam` — también funcionan.

**No sirven directamente**: `.fbx`, `.blend`, `.max`. Ábrelos en Blender y
exporta a `.glb`.

## 3. Cómo nombrarlos

Basta con que el nombre **empiece** por el tipo de unidad:

```
assets/models/helicopter.glb
assets/models/tank.glb
assets/models/osprey.glb
assets/models/rifleman.glb
assets/models/rocket.glb
assets/models/jet.glb
```

(`rifleman` es el del AK-47 y `rocket` el del lanzacohetes; si solo consigues un
modelo de soldado, cópialo con los dos nombres y se distinguirán por el color de
equipo.)

También valen `helicopter_apache.glb`, `tank-abrams.obj`, etc. Si prefieres
otro nombre, apúntalo con la clave `file` en `models.json`.

## 4. Ajustar escala y orientación

Casi ningún modelo descargado viene listo. La convención que espera la
simulación es:

- **Z arriba** (Panda3D es Z-up; casi todo lo descargado es **Y-up**).
- **El morro / cañón mirando a +Y.**

Se corrige sin tocar código, en [`models.json`](models.json):

```jsonc
"helicopter": {
  "length": 11.0,        // tamaño final en metros, da igual en qué unidades venga
  "hpr": [0, 90, 0],     // típico para pasar de Y-up a Z-up
  "tint": false,         // si el modelo ya trae sus propias texturas
  "nodes": { "main_rotor": "Rotor_Main", "tail_rotor": "Rotor_Tail" }
}
```

Si al arrancar el helicóptero sale tumbado o mirando hacia atrás, es solo
cuestión de probar `hpr`: `[0,90,0]`, `[180,90,0]`, `[90,0,0]`…

## 5. Para que se animen las piezas

Yo hago girar el rotor y apunto la torreta buscando **nodos por nombre** dentro
del modelo. Para que funcione, el modelo debe traer esas piezas como objetos
separados (no una malla fundida).

- Helicóptero: rotor principal y rotor de cola.
- Tanque: torreta (idealmente separada del casco, para que gire sola).
- Caza (`jet`): no necesita piezas separadas, se anima entero. Si el modelo
  trae tren de aterrizaje desplegado, mejor exportarlo retraído.
- Convertiplano (Osprey): las **dos góndolas** de las puntas de ala. Se
  bascūlan entre vertical (vuelo estacionario) y horizontal (modo avión), así
  que tienen que ser objetos separados y con su **pivote en el eje del ala**.
  Si el modelo trae los rotores como hijos de cada góndola, giran solos con
  ellas; llámalos `Proprotor` para que además roten.

Abre el `.glb` en Blender, mira cómo se llaman los objetos en el *Outliner* y
pon esos nombres en `nodes`. Si el modelo es una sola malla, no pasa nada:
simplemente no gira el rotor, el resto funciona igual.

## 6. Tamaño recomendado

La GPU aquí es modesta (Intel UHD por D3D12 en WSL). Mantén cada modelo por
debajo de ~50k triángulos. Los packs de Kenney/Quaternius rondan los 500–3000,
que es ideal.

## Comprobar que se cargó

Al arrancar, la consola dice de dónde salió cada unidad:

```
[assets] helicopter  -> helicopter.glb
[assets] tank        -> placeholder procedural
```
