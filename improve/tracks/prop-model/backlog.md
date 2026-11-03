# Backlog: prop-model

Hypotheses to try, best first. The reflect step reorders, adds and strikes items; mark a tried item with its
iteration number and result, do not delete it.

- [ ] Triangle budget from the picture: tell Claude to estimate the artist's budget from the object's size class
  and stay at the low end (the artist's median is 140-320 tris at every size; ours is 1.2-1.7x theirs).
- [ ] Silhouette first: a rule to match the outline from each of the four views before any detail, and to drop any
  part that does not change a silhouette (the brief says this; make it the first check in the loop).
- [ ] Proportions check: have Claude write the three overall dimensions it reads off the picture before modelling and
  compare the clay check's bounding box to them (size_ratio is already ~1.0, so this may only help part proportions).
- [ ] Fewer segments: lathes and cylinders at 8 segments by default, 12-16 only for the one big round hero part.
- [ ] No invented structure: say plainly that nothing absent from the picture is modelled (earlier runs grew an easel
  under a whiteboard, X-bracing on a shelf, a sign on a vending machine from the reference props).
- [ ] Shorter brief: remove rules that have not been shown to help and see whether the score holds (equal score with
  less text is a keep for a person).
- [ ] prop_kit helper for a common artist construction (a bevelled slab with an inset top face; a lathe from a
  profile list) if the notes show Claude rebuilding the same shape by hand each time.
