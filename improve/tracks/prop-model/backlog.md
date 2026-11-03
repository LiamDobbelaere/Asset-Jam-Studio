# Backlog: prop-model

Hypotheses to try, best first. The reflect step reorders, adds and strikes items; mark a tried item with its
iteration number and result, do not delete it.

- [x] ~~Triangle budget from the picture~~ #1: merged with item "fewer segments", discard (0.8712 vs 0.8708).
  Rules out tri count / segment count as a lever.
- [x] ~~No invented structure~~ #2: discard (0.8663 vs 0.8708). Rules out "don't add parts" rules as a lever.
- [ ] Silhouette first (next): a rule to match the outline from each of the four views before any detail, and to drop any
  part that does not change a silhouette (the brief says this; make it the first check in the loop).
- [ ] Proportions check: have Claude write the three overall dimensions it reads off the picture before modelling and
  compare the clay check's bounding box to them (size_ratio is already ~1.0, so this may only help part proportions).
- [x] ~~Fewer segments~~ #1: done together with the budget item, no gain.
- [x] ~~No invented structure (duplicate entry)~~ #2: done, discard.
- [ ] Model the interior/volume the artist shows (new): VendingMachine 0.79 and Whiteboard 0.70 are the weakest and
  the artist put detail Claude flattens (products, board depth/frame). Try a rule: when a face has a recess, window
  or frame in the picture, model it as real geometry (inset or cut), not a flat box.
- [ ] Shorter brief: remove rules that have not been shown to help and see whether the score holds (equal score with
  less text is a keep for a person).
- [ ] prop_kit helper for a common artist construction (a bevelled slab with an inset top face; a lathe from a
  profile list) if the notes show Claude rebuilding the same shape by hand each time.
