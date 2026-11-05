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

## Corroboration

An independent scene ≥ 15 days from the after scene is analysed against the same before scene.
Persistence = share of the evidence-mask pixels (probable clearing for ForestCheck, candidate
disturbance for MineWatch) (within the common valid area) detected
again (5×5 dilation for misregistration). ≥ 60% strong, < 30% weak.

## Verdict rules (`app/verification/evaluator.py`)

| | Forest | Mining |
|---|---|---|
| Key metric | probable clearing ha | candidate disturbance ha |
| Significant | ≥ 5 ha | ≥ 10 ha |
| Negligible | < 1 ha | < 2 ha |
| Change counts if | significant (and not shown transient) | significant **and** persistence ≥ 60% |

- Common valid area < 70% → inconclusive.
- "No change" claims: change → contradicted; negligible → supported (max medium confidence);
  otherwise inconclusive. "Change" claims: mirrored.
- Confidence = points (valid ≥ 90%, seasonal match, persistence, VLM agreement, margin);
  MineWatch capped at medium. No numeric probabilities.

All thresholds are starting heuristics, configurable, and shown in each report's methodology.
