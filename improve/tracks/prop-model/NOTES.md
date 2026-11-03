# Playbook: prop-model

The agent's own notes for this track: what the metric rewards, what has been tried, what to try next and how.
Rewritten by the loop's reflect step; a person may edit it too.

## What the metric rewards

- `pair_clay`: mean CLIP cosine similarity between the four clay views of the generated model and the four clay
  views of the artist's original, so shape, proportions and silhouette only; no texture, no colour.
- Decals (`k.decal`, Decal* materials) are stripped before rendering and counting, as the paint step strips them:
  painted marks cost nothing here either, so use them freely for flush details.
- Guards: every held-out prop must build (`failed` = 0) and the median triangle ratio generated / original must
  stay between 0.5 and 2.
- Known from earlier eval runs (see `memory/`): the artist's props are 22-1800 tris, 2-10 parts, mostly boxes and
  low-segment lathes; Claude tends to model 1.2-1.7x the triangles and sometimes adds structure the picture does not
  show. Modelling variance between runs with the same brief is real: calibrate before trusting a delta.

## Calibration (2026-11-03, Opus, unchanged brief, two full scores)

- pair_clay 0.8715 and 0.8700: the 12-prop mean is stable (spread 0.0015), so the keep threshold is the track's
  0.01. Per prop the spread is larger (WetFloorCone 88 vs 216 tris, GarbageBin 336 vs 548), so judge an idea by
  the mean, not by one prop.
- Weakest props both times: Whiteboard 0.71 (532 and 464 tris vs the artist's 184: built far more than a board on
  a frame), VendingMachine 0.79-0.81 (272-300 tris vs 1766: the artist modelled the inside / products, Claude a
  box), WaterCooler 0.82-0.85, ShelfSquare 0.85. Strongest: CardboardBox 0.94, Stool 0.93-0.94, CoffeeCup 0.92-0.94,
  WetFloorCone 0.91-0.92, DeskChair 0.91.
- tris ratio median 1.05-1.09: the budgets are about right overall; the misses are per object (Whiteboard,
  CoffeeCup 2x, DeskChair 1.3x over; VendingMachine 6x under).
- size ratio 0.56-0.59 everywhere: the artist's scene units are ~1.8x real metres (known; the studio scales the
  export). The metric ignores size, so do not spend an iteration on it.

## Harness problems

(none yet)

## Tried

(nothing yet; the first iteration scores the baseline)
