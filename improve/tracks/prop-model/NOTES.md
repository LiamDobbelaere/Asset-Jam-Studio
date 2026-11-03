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

## Harness problems

(none yet)

## Tried

(nothing yet; the first iteration scores the baseline)
