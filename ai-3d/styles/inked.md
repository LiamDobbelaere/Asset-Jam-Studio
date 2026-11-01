# Style guide: inked hand-paint

These rules come on top of the brief above and win where they disagree. The target is the look of a small indie
team's props: chunky, simple low-poly shapes whose detail lives in a hand-painted texture. A vending machine is a tall
box with its window inset and the buttons painted; a keyboard is one sloped slab with the keys painted; a cardboard
box is a box with a slightly wider lid box on top. The studio finishes the paint with flat muted colours, soft
painted shading and dark ink lines along every hard edge, so the edges you leave sharp become the drawn lines.

## Shape

- Triangle budget by size and importance, the lower end whenever it still reads:
  - small desk and hand items (book, binder, keyboard, mug, phone, box, bin): 12-250
  - mid-size props (water cooler, cabinet, vending machine, table, shelf, printer): 150-800
  - big soft furniture (office chair, couch, armchair, bed): 800-1800, the most anything gets
- Model only what changes the silhouette or stands out from a surface by more than ~3 % of the object's size: lids,
  rims, feet, legs, wheels and castors, armrests, taps, handles that stand off, pedals, a bottle on top. Everything
  flush with a surface is painted instead: keys, buttons, screens, labels, logos, vents, slots, drawer fronts and
  panel lines, seams and stitching, products behind glass. Use decals for these (below). Keys, keycaps and buttons
  are always decals, even when the picture draws them as raised blocks: a keyboard is one slab with painted keys.
- Few, simple primitives: boxes, cylinders and lathes with 8-16 segments (16-24 only for a big round hero shape such
  as a water bottle), tubes with 6 sides. A few big parts beat many small ones; merge what a player sees as one part.
- Hard objects (cabinets, machines, boxes, tables, shelves): straight sharp edges, no bevel or one small bevel
  (about 1 % of the size, 1 segment). Do not round hard boxes off: their sharp edges get the ink lines.
- Soft objects (seat and back cushions, armrests, couch arms, bags, bean bags): puffy and rounded, generous bevels
  (15-30 % of the part's thickness, 2-3 segments), slightly bulged tops (move the top vertices up with `k.edit`),
  so they read as upholstered.
- Proportions a little exaggerated and chunky rather than realistic: thicker slabs, legs and rims, bigger knobs,
  wheels and taps, puffier cushions, a slightly squat stance. Simplify rather than add.
- One or two insets for big recessed features (a vending machine window, a water cooler's tap niche, a monitor's
  screen bezel): inset the face and push it in a few cm with bmesh rather than building a frame of boxes.

## Painted details: decals

`k.decal(name, (w, h), loc, rgb, face="front")` puts a flat coloured mark on a surface: `loc` is its centre ON the
surface (it is lifted 1 mm off it), `face` is which way the surface faces (front = -Y, back, left = -X, right, top),
`(w, h)` is its size in that face's plane; `rot=` (euler, the mark's normal is local +Z) for a slanted surface,
`shape="disc"` for round ones. A decal is not geometry: it shows in the clay check and in the renders the paint starts
from, so the painted keys, buttons and labels land exactly there in every view; it is dropped from the final mesh
and costs no triangles.

- Place the details that make the object recognisable: a keyboard's key field (one dark rectangle, or a few rows),
  a vending machine's button panel, coin slot and logo panel, a box's label and hand hole, a cooler's taps' niche
  colour, drawer outlines, a screen, a display, a power button.
- Single details few and big: 3-12, nothing smaller than ~3 % of the object. Leave fine print, textures and small
  icons to the paint prompt.
- Repeating details are a loop of small decals, one per item with a gap between them: a keyboard's keys (rows of
  key decals, a long one for the spacebar), a button grid, vent slots, a vending machine's product rows. One big
  rectangle for "the keys" paints as a flat block, not as keys.
- Colours from the picture, contrasting enough with the surface underneath to read (a dark key field on a light
  keyboard, a light label on cardboard).

## Colours

- Muted, mid-saturation colours rather than toy-bright ones: steel blue, wine or brick red, sage or olive green, tan
  cardboard, warm and cool greys, off-white plastic. Dark parts dark grey or deep blue-grey, not pure black.
- One material per colour region, as the brief says; the finish keeps each part one flat colour plus painted
  shading, so give separately coloured regions their own part or decal.

## The paint answer

- `prompt`: what the object is, its parts and their colours as seen from any side ("a red vending machine cabinet
  with short black feet"). It goes to every view, so leave out anything only one side has: a window, a label or a
  button panel named here gets painted on the back too. The style's own painting words are added by the studio: do
  not add "stylized 3D game prop", "photorealistic", "pastel" or material words like "leather texture" or
  "brushed metal".
- `front`: the painted details on the front, matching your decals.
- `lock`: plain single-colour parts are usually not needed here: the finish flattens every part.
