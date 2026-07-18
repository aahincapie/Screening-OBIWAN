# References

Source material for the production code in `src/` and `config/`.

## `Tool OBIWAN Biomass Carbon Curves.ipynb`

The original Jupyter prototype (in `../Notebooks/`), 33 cells across three stitched
workflows. Only the third — the GEDI carbon engine — became the app; the first two
(VM0047 eligibility classification and a Hansen/MapBiomas transition exporter) produced
the intermediate raster the third consumed, which the app now builds in-memory.

### Where each notebook phase went

| Notebook | Cells | Production module |
|---|---|---|
| Phase 0 — config globals | 3, 12 | `config/defaults.py` — dataclasses bound to UI widgets |
| Phase 1 — AOI loading | 4, 14 | `src/aoi.py` — KML-first, equal-area geometry |
| Phase 9 — Hansen transitions | 6, 7 | `src/hansen.py` — Hansen only, `pixelArea()` accounting |
| Phase 1 — GEDI extraction | 14 | `src/gedi.py` — server-side stratification |
| Phase 2 — stratification | 16 | `src/gedi.py` — merged into extraction |
| Phase 3 — design estimator | 18 | `src/estimators.py` |
| Phase 4 — change / additionality | 20 | `src/estimators.py` |
| Phase 5 — trend & scenarios | 22 | `src/estimators.py` + `src/carbon_curve.py` |
| Phase 6 — exports | 24, 25, 26 | `src/exports.py` + `src/ui/charts.py` |
| Phase 7 — VM0047 engine | 28–32 | `src/vm0047.py` + `src/carbon_curve.py` |

### Behaviour deliberately changed in the port

1. **Area accounting.** The notebook's `class_areas_ha()` used a hardcoded
   `111320 × cos(lat)` metres-per-degree approximation. Acceptable at 15°N, materially
   wrong at high latitude. Replaced with `ee.Image.pixelArea()` server-side, and an
   equal-area projection for vector geometry.

2. **Hansen `gain` (class 21).** Hansen's gain band is 2000–2012 only and carries no
   year, so class 21 is temporally meaningless outside that window. The notebook noted
   this in a comment and then used class 21 to calibrate the entire carbon curve.
   Here, class 21 is restricted in code to curve calibration and can never be selected
   as project area.

3. **Hardcoded Earth Engine project.** `PROJECT_ID = "ee-geocaptain"` appeared in two
   cells. Replaced with per-user OAuth (`src/ee_auth.py`).

4. **Establishment mortality.** The port initially compounded per-stem survival across
   the whole rotation, which drove the curve downward in later decades — biomass
   cannot fall in a closed stand with no harvest, because a dying stem's growing space
   is captured by its neighbours, and `agb_max` already embeds that self-thinning.
   Mortality now applies only up to canopy closure. Caught by
   `tests/test_core.py::test_curve_is_monotonic_and_bounded`.

5. **Carbon-curve parameterisation.** The notebook's `Species` dataclass supported
   `logistic`, `linear_dbh`, `MAI` and `lookup` growth models, but nothing in the
   pipeline ever reached them — only `recovery_curve` was wired up. The app exposes
   intervention type, species mix, ecological zone, site index and site conditions as
   the levers that select and shape those models.

6. **Uncertainty.** The notebook applied a flat 10% ex-ante uncertainty regardless of
   whether the curve rested on local measurement or literature defaults.
   `src/vm0047.py::uncertainty_from_evidence` escalates it with the provenance tier.

### Behaviour deliberately preserved

- The hybrid design-based variance (`v_samp + v_pred`) and its documented
  approximation of the OBIWAN/L4B model-covariance term.
- The transition class codes `11 / 22 / 12 / 21`, so exported rasters stay
  interoperable with the upstream Jupyter workflow.
- The `recovery_curve` exemption from the `age <= 0 → 0` short-circuit. The notebook
  fixed this bug during its own development; the reasoning is preserved in
  `src/carbon_curve.py::ResolvedSpecies.agb_at`.
- The VM0047 Eq. 30 deduction ordering.

## UI reference

**BioTrace AI** — [live](https://estimatingbiomasschange.vercel.app/) ·
[repo](https://github.com/gyan1201/Estimating-Biomass-Change-with-GEDI-and-the-OBIWAN-API)

React 19 + Vite + Leaflet SPA. `src/api.js` is a thin client over six REST endpoints;
all computation happens in an OBIWAN backend not present in that repository, hardcoded
to Alabama coverage. It is a UI reference only — no reusable analysis code.

**Adopted:** persistent KPI strip that survives tab changes · tabbed results instead of
one long scroll · actionable empty states · always-reachable exports · a glossary for
MRV jargon.

**Rejected:** draw-a-polygon as the only AOI input (KML upload is the requirement) ·
AI chat and novelty panels · hardcoded regional coverage · **no uncertainty displayed
anywhere** — for MRV work, a carbon number without a confidence interval will be
quoted as a measurement, so every sample-derived figure in this app carries its CI.

## Literature

- Hansen, M. C. et al. (2013). High-Resolution Global Maps of 21st-Century Forest
  Cover Change. *Science* 342(6160), 850–853.
- Dubayah, R. et al. GEDI L4A Footprint Level Aboveground Biomass Density, Version 2.1.
  ORNL DAAC.
- Patterson, P. L. et al. (2019). Statistical properties of hybrid estimators proposed
  for GEDI. *Environmental Research Letters* 14(6).
- IPCC (2019). *2019 Refinement to the 2006 IPCC Guidelines for National Greenhouse Gas
  Inventories*, Vol. 4, Ch. 4 (Forest Land). Tables 4.4, 4.7, 4.9, 4.10.
- Verra. *VM0047 Afforestation, Reforestation and Revegetation*, v1.0.

> The default growth parameters in `config/ecological_zones.py` and
> `config/species.py` are **screening approximations informed by** the IPCC tables,
> not transcriptions of them. Verify every value against the current source tables
> before using this tool's output in a submission.
