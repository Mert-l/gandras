# Methodology

All numbers come from Sentinel-2 L2A (`sentinel-2-c1-l2a`, Element 84 Earth Search).

## Imagery

- **AOI → grid:** square AOI around a centre, projected to its UTM zone; 10 m grid snapped to the
  resolution. Pixel area comes from the affine transform (100 m² at 10 m).
- **Reading:** only the AOI window of each COG, reprojected with `WarpedVRT` onto the grid
  (bilinear for reflectance, nearest for SCL). Scenes from different MGRS tiles align
  (measured residual shift at Bingham: ~0.15 px).
- **Calibration:** `reflectance = DN × scale + offset` from each asset's `raster:bands`
  (this collection: 0.0001 / −0.1). DN = nodata → NaN, never 0.
- **Masking:** SCL classes no-data, saturated, cloud shadow, cloud medium/high, thin cirrus,
  snow/ice are invalid, plus a 50 m buffer around cloud/shadow. Comparisons use the intersection
  of valid pixels on both dates.
- **Scene choice:** scene-wide cloud is only a pre-filter. Candidates are ranked by local
  invalid % over the AOI (≤ 15% usable; ≤ 1% preferred), then by distance from the target date.
  Example: a scene with 3.1% scene-wide cloud was 71% obscured over the Bingham AOI.
- **Previews:** one shared 2–98 % stretch from pixels valid on both dates, so unchanged ground
  renders identically. Purple = masked.

## ForestCheck

- NDVI = (B08 − B04)/(B08 + B04); ΔNDVI = after − before.
- Candidate vegetation loss: NDVI_before > 0.45, NDVI_after < 0.25, ΔNDVI < −0.20; water excluded;
  3×3 opening; regions < 0.5 ha removed.
- **Forest-like before:** NDVI ≥ 0.70 and B11 < 0.18 (canopy is dark in SWIR; green pasture is not —
  measured at a test site at −9.35, −64.45, Jul 2020: forest NDVI 0.83/B11 0.13 vs pasture 0.58/0.25).
- **Probable clearing** = candidate loss ∧ forest-like before ∧ BSI rise > 0.05, then the same
  minimum-area filter. Only this is used as evidence of clearing: it is the headline area, the red
  numbered regions on the overlay/mask, and the mask tested for persistence. Other vegetation loss is reported but treated as possibly pasture/crop/seasonal.

## MineWatch

- Relative radiometric normalization of the after scene (per-band median ratio over valid pixels).
- Change magnitude = ‖Δ(B04, B08, B11)‖; threshold = max(0.06, median + 5 × 1.4826 × MAD),
  i.e. derived per site.
- BSI = ((B11 + B04) − (B08 + B02)) / ((B11 + B04) + (B08 + B02)); ΔBSI and ΔNDVI per region.
- 3×3 opening; regions < 1 ha removed. Each region gets a descriptive spectral pattern
  (`change_within_bare_ground`, `vegetated_to_bare`, …), not a cause.
- Output is **candidate surface disturbance**, never "excavation".

## Water (WaterCheck)

- MNDWI = (B03 − B11)/(B03 + B11); water where MNDWI > 0 on each date (configurable).
- New water = not water before ∧ water after; lost water = the reverse; 3×3 opening; regions < 0.5 ha removed.
- Evidence follows the claimed direction (increase / decrease / any); the other direction is shown amber.
- Reports water extent on both dates and the net change; extent only, never volume or depth.

## Construction & built-up (BuildCheck)

- Robust multispectral change (MAD threshold, radiometrically normalized) restricted to surfaces that are
  non-vegetated (NDVI < 0.30) and not water afterwards.
- Evidence = new built-like surface (roofs, paving): see `app/indicators/built_up.py` for the exact rule,
  tuned on real construction sites and a mine (where scraped soil must not count as a building).
- Cleared or bare ground / earthworks is reported in amber and is not evidence of a building.
- Cannot resolve single small buildings (< ~0.5 ha), and never establishes completion, occupancy, permits
  or legality — "illegally built" claims are tested only for whether new built surface appeared.

## Fire (BurnCheck)

- NBR = (B08 − B12)/(B08 + B12); dNBR = NBR_before − NBR_after.
- Evidence: dNBR > 0.27 (moderate-low severity and above) with an NDVI drop > 0.1; regions ≥ 1 ha;
  area by severity class. Scars fade with regrowth.

## Vegetation cover

- NDVI loss (> 0.45 → < 0.25, Δ < −0.2) and gain (the reverse) for any vegetation; direction from the claim.

## Generic fallback

- Same change detector as surface disturbance. Verdicts limited to supported (no-change claim,
  negligible change, low confidence) or inconclusive — never contradicted.

## Corroboration

An independent scene ≥ 15 days from the after scene is analysed against the same before scene.
Persistence = share of the evidence-mask pixels (probable clearing for ForestCheck, candidate
disturbance for MineWatch) (within the common valid area) detected
again (5×5 dilation for misregistration). ≥ 60% strong, < 30% weak.

## Verdict rules (`app/verification/evaluator.py`)

Key metric is the indicator's `evidence_ha` in the claimed direction. Significant / negligible areas:
forest 5 / 1 ha, vegetation 5 / 1, surface 10 / 2, built-up 2 / 0.5, water 5 / 1, burn 5 / 1, generic 5 / 1.
Change counts if significant, not shown transient (< 30% persisting), and — for surface, built-up and
generic — confirmed by ≥ 60% persistence in an independent observation.

- Common valid area < 70% → inconclusive.
- "No change" claims: change → contradicted; negligible → supported (max medium confidence);
  otherwise inconclusive. "Change" claims: mirrored.
- Confidence = points (valid ≥ 90%, seasonal match, persistence, VLM agreement, margin);
  capped per indicator (see docs/architecture.md). No numeric probabilities.

All thresholds are starting heuristics, configurable, and shown in each report's methodology.
