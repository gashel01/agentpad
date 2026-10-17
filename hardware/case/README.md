# Case v2

Square 116 × 116 mm case, R15 rounded corners, sitting on a round base tilted 6°.
All the electronics are mounted **under the plate** (top mount): the bottom is passive
and closes with a clip-on hatch. Only 4 screws go through the plate, at the corners
(M2 × 8 mm maximum, the holes are blind). The halo shines out through a 45° groove between the case's
two lips.

## Parts to print

| File | Part | Qty | Material |
|---|---|---|---|
| `v2_bloc_plaque/01_plaque_dessus.stl` | Top plate | 1 | Light grey PETG |
| `v2_bloc_plaque/02_grille_A_rangees.stl` | Row wire guide | 1 | PETG |
| `v2_bloc_plaque/03_grille_B_colonnes.stl` | Column wire guide | 1 | PETG |
| `v2_bloc_plaque/04_matrice_led.stl` | Key LED holder | 1 | PETG |
| `v2_bloc_plaque/05_matrice_couvercle_rang0..2.stl` | Matrix covers | 3 | PETG |
| `v2_bloc_plaque/06_halo_*_bloc.stl` | Halo blocks (front, back, left, right) | 4 | PETG |
| `v2_bloc_plaque/07_halo_*_couvercle.stl` | Halo covers | 4 | PETG |
| `v2_bloc_plaque/08_knob.stl` | Knob (6 mm D shaft, 10 mm flat) | 1 | White PLA |
| `v2_bloc_plaque/09_capuchon_joystick.stl` | Joystick cap | 1 | White PLA |
| `v2_fond/10_fond_boitier_base.stl` | Case and round base | 1 | Light grey PETG |
| `v2_fond/11_trappe_fond_clipsee.stl` | Bottom hatch | 1 | Light grey PETG |
| `v2_keycaps/10_agent_lisse_x8.stl` | Agent keys, plain (print 8) | 8 | Translucent white PLA |
| `v2_keycaps/01..08_agent_*.stl` | Variant: numbered agent keys | 8 | Translucent white PLA |
| `v2_keycaps/20..23_*.stl` | Bottom row: Bolt, Confirm, Cancel, Mic | 4 | Light grey PETG (slightly translucent) |
| `v2_diffuseur/14_diffuseur_anneau.stl` | Halo diffuser, one piece | 1 | Translucent white |

The 8 agent keys are in translucent white PLA: they take on the color of their
session's LED. Their tops are 0.6 mm thick (any thinner and the bridge does not print).

The keys can be regenerated with `v2_keycaps/keycaps_generateur.py` (run it in Blender).

## Halo diffuser

A single band that wraps the whole case, from the top of the plate down to the bottom
lip, and clips in from the outside. It is open in one place only, at the back along the
cable: the two ends overlap there, so no gap shows once it is mounted. The plastic is
thin enough to flex open around the case.
