# Channel model — what is modelled, what is not

*Audit date: 2026-10-29. Scope: the air-to-ground propagation model in
`channel_bridge.py` and the four world modules it imports.*

This document exists because RQ2 asks about terrain **and** near-ground antenna
height, and the two halves are in very different states. The terrain half is
built and measured. The antenna-height half is not modelled at all. Everything
below is traceable to a file and line; nothing is inferred from behaviour.

**Summary:** the simulator has no ground-terminal height dimension to vary, so
any claim of the form *"altitude matters differently near the ground"* is
currently unsupported — not contradicted, unsupported. Whether that is a defect
or a scoped-out assumption depends on what RQ2 commits to.

---

## 1. The model, in full

One shared core (`channel_bridge.py:63`), with terrain entering only through two
arguments:

```
PL   = FSPL(d_3d) + p_LoS·η_LoS + (1 − p_LoS)·η_NLoS + excess_dB
FSPL = 20·log10(d_3d) + 20·log10(f) − 147.55        f = 3.31968 GHz
d_3d = sqrt(d_horiz² + (h_uav − 0.5)²)
θ    = atan2(h_uav − 0.5, d_horiz)
```

with `η_LoS = 1.0 dB` and `η_NLoS = 20.0 dB` (`channel_bridge.py:45-46`).

### 1.1 Per-terrain coupling

`world_channel(uav, ue) -> (los_blocked, excess_db)`, dispatched at
`channel_bridge.py:256-296`:

| terrain | LoS decision | excess loss | source |
|---|---|---|---|
| `open_field` | **probabilistic** — Al-Hourani sigmoid on θ | none | `channel_bridge.py:291` |
| `urban` | deterministic — 3-D ray/AABB slab test, 12 boxes, 1 m margin | none | `urban_buildings.py:71` |
| `forest` | NLoS when canopy depth ≥ 2 m | **Weissberger (1982)**: `0.45·f^0.284·d` for d ≤ 14 m, `1.33·f^0.284·d^0.588` for 14–400 m; depth capped at 70 m | `forest_trees.py:115` |
| `hills` | NLoS when diffraction ≥ 6 dB | **ITU-R P.526** single knife-edge `J(v) = 6.9 + 20·log10(√((v−0.1)²+1) + v − 0.1)`, worst hill only, capped 40 dB | `terrain_hills.py:53` |
| `indoor` | deterministic NLoS inside footprint | **3GPP TR 38.901 O2I**: 9 dB + 0.5 dB per metre of depth, capped 40 dB | `indoor_building.py:92` |

Obstacle geometry is imported from the same modules that generate the world SDF,
so the radio and the visuals cannot drift apart. That part of the design is sound
and worth keeping.

### 1.2 Fading

Log-normal shadowing, σ = 2 dB LoS / 5 dB NLoS, correlated over **distance
moved** rather than wall-clock time (Gudmundson, decorrelation length 20 m,
`channel_bridge.py:58-60`). Driving it off ticks instead made a stationary
robot's loss flicker every update; the distance-correlated form was the fix.

### 1.3 The complete list of loss terms

| term | magnitude | terrains |
|---|---|---|
| FSPL | distance-driven | all |
| LoS/NLoS excess | 1.0 / 20.0 dB | all |
| Log-normal shadowing | σ 2 dB LoS, 5 dB NLoS | all |
| Weissberger foliage | 0 – ~50 dB | forest |
| ITU-R P.526 knife-edge | 0 – 40 dB | hills |
| TR 38.901 O2I penetration | 9 dB + 0.5 dB/m | indoor |
| Per-robot baseline offset | 8 / 13 / 10 / 15 dB UL | all |

Nothing else. A grep across every `.py` for `Fresnel|two-ray|ground reflect|body
block|antenna` returns only unrelated `TX_REF` constants.

---

## 2. Antenna height: the gap

### 2.1 There is no antenna

No model SDF in `models/` contains an antenna link, and no code reads one. Three
different heights are in play, and they disagree:

| quantity | value | where |
|---|---|---|
| radio UE height | **0.5 m, hard-coded** | `channel_bridge.py:41` — `UE_HEIGHT = 0.5` |
| Gazebo spawn z | **0.1 m** | `start_full_stack.sh:205` |
| tallest robot part | **0.18 m** (lidar link) | `models/sar_burger/model.sdf:88` |

`start_full_stack.sh:181` spawns `models/sar_burger/model.sdf`, which wraps the
stock `turtlebot3_burger` and adds sensor links at z = 0.17–0.18 m. With a
0.033 m wheel radius, the robot's highest point is ~0.18 m — so the radio's 0.5 m
is nearly **three times the robot's physical height**, and it is a free-standing
constant rather than anything derived from the model.

### 2.2 The two heights are used inconsistently

The robot's **actual** pose z (≈ 0.1 m) is passed to `world_channel(uav, ue_pos)`
and therefore drives the blocking ray, the foliage-depth integral and the
knife-edge geometry. But `a2g_path_loss` is called at `channel_bridge.py:526`
without `h_ue`:

```python
pl, los_p, d_3d, theta = a2g_path_loss(d_horiz, uav_h,
                                       los_blocked=blocked,
                                       excess_db=excess)
```

so FSPL and the elevation angle fall back to the 0.5 m default. **Obstruction
geometry and path-loss geometry are computed at two different robot heights.**

### 2.3 No terrain model is height-sensitive at the ground end

`h_ue` appears three times, all inside `a2g_path_loss`, and no caller ever passes
it. None of the four world modules contains a UE-height term. There is no
mechanism in the simulator by which lowering or raising a ground terminal changes
its link.

### 2.4 Al-Hourani cannot supply it

`open_field` uses the Al-Hourani LoS-probability sigmoid
(`channel_bridge.py:70`):

```python
los_p = 1.0 / (1.0 + A_COEFF * math.exp(-B_COEFF * (theta - A_COEFF)))
```

Two issues:

**(a) The constants are the Urban set.** `A = 9.61`, `B = 0.16`, `η_LoS = 1.0`,
`η_NLoS = 20` are exactly Al-Hourani, Kandeepan & Lardner (2014) **Urban**
environment parameters. The world they parameterise here is `sar_open_field`.
The Suburban set (4.88 / 0.43 / 0.1 / 21) would be the defensible choice for open
country. Nothing in the repo explains the selection.

**(b) It has no height dependence to exploit.** The sigmoid is a statistical
LoS-probability fit for a low-altitude platform over a ground user in a built-up
area, with the ground terminal's height **absorbed into the empirical
constants**. It therefore cannot express near-ground effects for a 0.17 m robot,
whatever height is fed to it — and what is fed to it is the fictitious 0.5 m.

---

## 3. Other things that are not modelled

- **Multipath.** `select_channel_type()` (`channel_bridge.py:93`) computes
  TDL_D/TDL_A per cycle, but every OAI model is configured `type = "AWGN"`
  (`oai_configs/gnb.sa.band78.rfsim.conf:134` onward) and type is not settable at
  runtime (`channel_bridge.py:610`; `docs/A2G_CHANNEL.md §3`). **There is no
  multipath in any run** — the TDL selection is logged and discarded.
- **Ground-reflection / two-ray interference.** Absent. This is the standard,
  cheap mechanism that makes a near-ground link height-sensitive.
- **Fresnel-zone ground clearance.** Absent. The P.526 term uses the
  Fresnel-Kirchhoff parameter *v* for hilltop diffraction, which is a different
  phenomenon from ground-plane Fresnel obstruction.
- **Body blockage** between robots, or robot self-shadowing. Absent.
- **Rubble and debris** scatter or partial obstruction. Absent.
- **Antenna gain, radiation pattern, polarisation.** Absent — the link budget has
  no antenna terms at all.

---

## 4. Terrain coverage against the proposal

| proposal terrain | closest here | gap |
|---|---|---|
| open field | `sar_open_field` | parameterised with Al-Hourani **Urban** constants (§2.4a) |
| urban rubble | `sar_urban` | models **intact buildings** as clean axis-aligned boxes with a binary block. No rubble: no debris scatter, no partial obstruction, no diffuse loss. Rubble is a *low-height, near-ground* obstruction class — exactly the regime a fixed 0.5 m UE height cannot represent |
| collapsed / corridor-like | `sar_indoor` | the world exists and is the closest structural match, but **it is not in the ML dataset**. The four ML campaigns contain forest (5 runs), hills (5), open_field (9), urban (5) and **zero** indoor runs |
| — | `sar_forest`, `sar_hills` | extra terrains, not in the proposal |

Two of the three proposal terrains are loosely covered; the corridor-like one has
a world built but no data behind it.

---

## 5. What the logs actually produce

The logged `*_ploss_dB` is the post-EMA, post-shadowing value sent to the
rfsimulator, so the dataset can be regressed directly. Fitting

```
ploss − baseline = A + 10·n·log10(d_3d)
```

over all 55,046 robot-cycles in the four ML campaigns, excluding rows clamped at
the 45 dB ceiling (`scripts/fit_path_loss.py`):

| terrain | n | % at 45 dB clamp | exponent *n* | residual σ (dB) | R² | Δ per +10 m UAV altitude |
|---|---|---|---|---|---|---|
| open_field | 16,759 | 0.0 % | **3.61** | 4.28 | 0.58 | −3.28 dB |
| forest | 8,632 | 0.2 % | 2.14 | **11.75** | **0.04** | −2.52 dB |
| urban | 11,232 | 0.0 % | 1.89 | 8.28 | 0.10 | −1.05 dB |
| hills | 18,423 | 0.7 % | **1.50** | 5.45 | 0.12 | −0.38 dB |

Free space is 2.00. **These are effective exponents, not physics**, and three of
the four are badly specified:

- **open_field 3.61** is inflated by the Al-Hourani sigmoid, which adds up to
  20 dB of NLoS excess at low elevation angle — i.e. at long horizontal range.
  Distance and elevation are collinear, so the sigmoid's angle dependence is
  absorbed into the range slope.
- **forest R² = 0.04** means distance explains essentially nothing. The loss is
  canopy depth, a geometric integral nearly uncorrelated with range.
- **hills 1.50**, below free space, is a control artefact: the UAV climbs when a
  ridge blocks it, so the longest ranges are also the highest-altitude,
  best-cleared geometries.

**The residual σ column is not a shadowing σ.** It contains the 19 dB LoS/NLoS
step and the terrain clutter term as well as the 2/5 dB fading, which is why
forest reads 11.75 dB against a configured 5 dB.

---

## 6. Validation status

**Nothing in the propagation model has been validated against measurement or
against a reference model.** What exists:

- Citations are to **formula provenance**, not validation: Weissberger (1982),
  ITU-R P.526, Al-Hourani et al. (2014), Gudmundson correlation.
- **TR 38.901 is cited once**, for the indoor O2I term
  (`indoor_building.py:6, :74`) — the one terrain with no ML data.
- `docs/A2G_CHANNEL.md §2` is the only measurement table, and it measures the
  **OAI stack's** response to a commanded path loss (throughput vs dB, RTT vs
  sample offset), not the propagation model's agreement with anything external.
  It also records ±50 % run-to-run throughput variance.
- `docs/SYSTEM_ARCHITECTURE.md` and `README.md:525` describe the model; neither
  claims validation.

The nearest thing to a check is the noise-floor recalibration at `README.md:181`,
which is internal to the stack.

---

## 7. What would close the gap

Both of these invalidate the existing dataset, so they are a new campaign rather
than a patch. Listed in order of effort.

1. **Thread the real UE height through.** Make `h_ue` a per-robot value read from
   the Gazebo pose — a one-line change at `channel_bridge.py:526` plus removing
   the default. This fixes the §2.2 inconsistency but does **not** by itself make
   the link height-sensitive, because no loss term reads height.
2. **Add a two-ray ground-reflection term.** The standard, cheap way to give a
   near-ground link genuine height sensitivity, and the minimum needed for an
   antenna-height claim to mean anything.
3. **Re-parameterise open_field** with the Al-Hourani Suburban constants, or
   state explicitly why Urban is being used.
4. **Collect indoor runs** if the corridor-like terrain is to be claimed at all.
5. **Model rubble** as a distinct low-height obstruction class if "urban rubble"
   is to be distinguished from "urban".

---

## 8. Verdict

The **terrain-dependence** half of RQ2 is genuinely done. Each world has a
physically motivated, independently sourced excess-loss mechanism; geometry is
shared between radio and visuals so they cannot drift apart; and the resulting
sign flip is measured and reproduced by a terrain-blind model.

The **near-ground antenna-height** half is absent, in four separate ways:

1. UE height is a hard-coded 0.5 m constant — never varied, never per-terrain,
   and ~3× the robot's physical height.
2. The robot's real z drives obstruction geometry but not path loss.
3. No propagation mechanism is height-sensitive at the ground end: no two-ray, no
   Fresnel clearance, no near-ground clutter, no body blockage.
4. The one model touching ground-terminal geometry — Al-Hourani — absorbs UE
   height into empirical constants and so cannot express it, and is running Urban
   constants in the open-field world.

---

*Every file:line reference above was checked against the working tree on
2026-10-29. Section 5 reproduces with `python3 scripts/fit_path_loss.py`.*
