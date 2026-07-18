# 🌱 Screening-OBIWAN

**Ex-ante screening for reforestation (ARR) carbon projects — anywhere on Earth.**

Upload a project boundary, and this tool tells you how much land is suitable for
reforestation, how much biomass is there now, how much carbon a restoration
intervention would accumulate, and what that is worth in VM0047 credits — with
confidence intervals and an honest label on how much the numbers can be trusted.

Built from a Jupyter prototype combining GEDI spaceborne lidar, Hansen Global Forest
Change, and the OBIWAN design-based estimation approach.

---

## What makes it globally applicable

Forest-change classification uses **only** the Hansen Global Forest Change dataset.
One 30 m global product, one methodology, identical class semantics in Guatemala,
Ghana and Guangxi. No national land-cover assets to configure, no regional gaps, no
per-country code paths.

GEDI biomass sampling is used where it exists (roughly ±51.6° latitude, its ISS orbit
limit). Outside that band — or where footprints are too sparse — the carbon curve
falls back to IPCC ecological-zone defaults, and **every result is labelled with which
tier produced it**:

| Tier | Meaning |
|---|---|
| 🟢 `gedi_calibrated` | Ceiling and growth rate both fit to this AOI's own GEDI record |
| 🟠 `gedi_partial` | One of the two came from GEDI, the other from IPCC defaults |
| 🔴 `ipcc_default` | No usable GEDI — literature values only, **indicative results** |

The uncertainty deduction escalates automatically with the tier (10% → 20% → 30%),
because claiming VM0047's 10% ex-ante minimum on a curve with no site measurement
is not defensible.

---

## The reforestation focus

Hansen transitions are classified into four classes over your chosen window:

| Code | Class | Role |
|---|---|---|
| `22` | Stable non-forest | **Plantable** — available for new planting or ANR |
| `12` | Forest loss | **Plantable** — recently cleared, requires restoration |
| `11` | Stable forest | Ceiling reference only — never eligible project area |
| `21` | Forest gain | Regeneration calibration only — never creditable |

Only `22` and `12` can be selected as project area. Stable forest is offered nowhere
in the UI, because it is not eligible for ARR.

> **Hansen caveat, stated plainly:** the `gain` band covers 2000–2012 only and carries
> no year, so class `21` cannot be restricted to your analysis window. This tool uses
> it *solely* to calibrate the regeneration curve, never as project area. The source
> notebook flagged this in a comment and then calibrated on it anyway; here the
> restriction is enforced in code.

---

## The carbon curve (Phase 7)

Five independent levers shape the accumulation curve, all adjustable from the sidebar:

1. **Intervention type** — Active planting (logistic, from bare ground, establishment
   lag) · Assisted Natural Regeneration (recovery curve, head start from existing
   rootstock) · Mixed/enrichment (weighted blend)
2. **Ecological zone** — 10 FAO zones setting the AGB ceiling, growth rate and
   root:shoot ratio. Auto-suggested from AOI latitude and tree cover; always
   overridable.
3. **Site index** — productivity relative to the zone average (0.5–1.5)
4. **Site conditions** — soil quality, water stress, fire risk, grazing pressure.
   Multiplicative on growth and ceiling, additive on mortality.
5. **Species mix** — up to 5 cohorts with per-cohort allometry, growth model, stem
   density, mortality and harvest cycle, weighted by area share.

Where GEDI is available, the ceiling is fit to local mature forest and the recovery
rate to local regenerating land, so the curve reflects what *this* landscape actually
does rather than what the literature says a landscape like it should do.

---

## Quick start

**Prerequisites:** Python 3.10+, and a Google account registered for
[Earth Engine](https://earthengine.google.com/signup/) with a Cloud project.

```bash
git clone https://github.com/<you>/Screening-OBIWAN.git
cd Screening-OBIWAN

python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate

pip install -r requirements.txt

# One-off Earth Engine sign-in (recommended — avoids the in-app OAuth flow)
earthengine authenticate

streamlit run app.py
```

The app opens at <http://localhost:8501>. Enter your Earth Engine project ID, upload a
KML, and press **Run screening**. There is a demo AOI (Petén, Guatemala) if you want
to explore before uploading anything.

### Authentication

Each user signs in with their **own** Google account and their **own** Earth Engine
project, so quota and billing stay with them. Nothing is hardcoded and no credentials
are stored by this app beyond your machine's standard Earth Engine credentials file.

If `earthengine authenticate` is unavailable, the app offers a browser OAuth flow
(generate link → approve → paste code). For headless deployment, set
`EE_SERVICE_ACCOUNT_JSON` in the environment.

---

## Input

**KML up to 1 MB**, containing polygons. KMZ, GeoJSON, GeoPackage and zipped
shapefiles also work. Boundaries are reprojected to EPSG:4326, repaired if invalid,
optionally dissolved, simplified and buffered.

Areas are computed in an **equal-area projection**, not the `111320 × cos(lat)`
approximation the prototype used — that approximation drifts badly outside the tropics
and would have made a globally-applicable tool quietly wrong at high latitude.

---

## Output

- **VM0047 workbook** (`.xlsx`) — Summary, Project Parameters, Carbon Curve (charted),
  ER Projections (charted), Scenario Comparison, Land Cover, GEDI Trend
- **All tables** (`.zip`) — every DataFrame as CSV, plus a Markdown methods note
- **Run configuration** (`.json`) — reproduce any run exactly

Every export carries the methods note recording the provenance tier, the parameters
used, and the limitations of the underlying data.

---

## Project structure

```
Screening-OBIWAN/
├── app.py                      Streamlit entry point (presentation only)
├── requirements.txt
├── config/                     User-adjustable parameters — no analysis logic
│   ├── defaults.py             AppConfig dataclasses + validation
│   ├── ecological_zones.py     10 FAO zones + site modifiers
│   └── species.py              Intervention presets + species templates
├── src/
│   ├── pipeline.py             Phase sequencing (framework-independent)
│   ├── aoi.py                  KML/KMZ ingestion, equal-area geometry
│   ├── hansen.py               Forest-change transitions       [Earth Engine]
│   ├── gedi.py                 Biomass sampling                [Earth Engine]
│   ├── estimators.py           Design-based statistics         [pure]
│   ├── carbon_curve.py         Accumulation modelling          [pure]
│   ├── vm0047.py               Quantification engine           [pure]
│   ├── exports.py              CSV / XLSX / methods note
│   ├── ee_auth.py              Per-user OAuth
│   └── ui/                     Sidebar, charts, maps, components
├── references/                 Source notebook + method notes
└── tests/                      29 tests, no credentials required
```

Everything from `estimators.py` downward is pure Python — no Earth Engine, no
Streamlit — so the statistics and accounting, the parts most likely to be wrong in a
carbon tool, are testable without a network round-trip.

```bash
python -m pytest tests/ -v
```

---

## Method

Per-stratum mean AGBD is a **design-based estimate** with a hybrid standard error:

```
ȳ      = mean(yᵢ)                       estimated mean AGBD
v_samp = s²/n                           design-based sampling variance
v_pred = mean(uᵢ²)/n                     GEDI model prediction variance
SE     = √(v_samp + v_pred)
95% CI = ȳ ± 1.96·SE
```

Differencing two interpolated biomass maps confounds model error with real change and
yields no defensible uncertainty. GEDI is a *sample*; treating it as one is the whole
point.

> **Known limitation:** the full OBIWAN/L4B estimator additionally folds in GEDI's
> model coefficient covariance (`a′Ca`). Here that term is approximated by the
> per-footprint prediction variances, so reported standard errors are **slightly
> optimistic**. The substitution point is marked in `src/estimators.py`. This is
> precisely why `src/vm0047.py` applies its own sample-size-aware uncertainty floor
> rather than trusting these SEs directly.

---

## Scope and limitations

This is an **ex-ante screening tool**. It is not a validated VM0047 quantification and
does not substitute for a PDD, a field inventory, or assessment by a validation and
verification body.

- Default growth parameters are **screening approximations**, not verified Tier 1
  values. Verify against IPCC 2019 Refinement Vol. 4 Ch. 4 Tables 4.4/4.7/4.9/4.10
  before any submission. Every one is editable in the UI.
- Hansen "loss" means stand-replacing canopy removal, which includes plantation
  harvest and natural disturbance. It is not a deforestation figure.
- Land that became forest between 2000 and your window start via processes Hansen's
  gain band missed is misclassified as non-forest.
- GEDI does not exist before 2019 and not beyond ±51.6° latitude. Pre-2019 biomass is
  never inferred.

Quote the **sensitivity range**, not the point estimate.

---

## Data sources & credits

- **Hansen Global Forest Change** — Hansen et al. (2013), *Science* 342:850–853
- **GEDI L4A Footprint Aboveground Biomass Density** — Dubayah et al., ORNL DAAC
- **Design-based estimation** — Patterson et al. (2019); GEDI L4B ATBD
- **VM0047** — Afforestation, Reforestation and Revegetation, Verra
- **OBIWAN** — [yang.users.earthengine.app/view/obiwan](https://yang.users.earthengine.app/view/obiwan)
- UI reference — [Estimating Biomass Change with GEDI and the OBIWAN API](https://github.com/gyan1201/Estimating-Biomass-Change-with-GEDI-and-the-OBIWAN-API)

## Licence

MIT — see `LICENSE`.
