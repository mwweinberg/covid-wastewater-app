# Wastewater Watch

Android app that shows SARS-CoV-2 (plus influenza A/B and RSV) wastewater levels for Berlin, New York City and Washington DC, with a home-screen widget, a prevalence readout against a reference threshold, trend detection and a "wave starting" alert.

## How it works

* `pipeline/build_data.py` runs twice a day in GitHub Actions. It slices three public feeds into small per-city JSON files under `data/`:
  * **Berlin**: Robert Koch-Institut [AMELAG](https://github.com/robert-koch-institut/Abwassersurveillance_AMELAG) per-site data (flow-normalised), blended across the Ruhleben, Waßmannsdorf and Schönerlinde plants with fixed plant offsets and population weights.
  * **New York City**: [NYC Open Data f7dc-2q9f](https://data.cityofnewyork.us/d/f7dc-2q9f), 14 DEP plants, digital PCR only; weeks NYC has not yet published are filled from CDC's level for the same plants (marked provisional). Flu/RSV and the official category come from CDC.
  * **Washington DC**: CDC [Wastewater Viral Activity Level](https://data.cdc.gov/d/atcp-73re) for the Blue Plains plant (DC plus Maryland and Virginia suburbs).
* **Comparable period only.** Every city changed laboratory or method (Berlin April 2024, NYC April 2023, DC June 2024). Only data from the current period is published and shown; percentiles compare against that period.
* **Threshold readout** (`pipeline/wastewater_threshold.py`, parameters frozen in `pipeline/threshold_config.json`): each city's 2-week trailing wastewater value is converted to a best-guess share of people infectious and placed in one of five bands around Bob Wachter's 2022 threshold (10 reported cases per 100,000 per day ≈ 0.28% infectious): *Well below / Just below / Right around / Just above / Well above*, with "About 1 in N people infectious" and the percentile since the comparable date. Berlin is projected to today with a damped trend; NYC and DC carry the last value. NYC ("Very low confidence") and DC ("Low confidence") are calibrated on hospital admissions and carry labels.
* **Alarms.** A new lab change (Berlin `laborwechsel`, NYC `technology`, DC CDC `source`), a silent NYC method shift against CDC, or a CDC history rewrite that is not a rescaling withholds the readout. The app shows a banner instead and sends a high-priority notification. Fixing it requires a refit in the methods project.
* The app (`app/`) fetches the files, caches them, and does the rest on-device:
  * **Momentum**: three consecutive weekly rises, at least +10 % per week, and 3x the trailing 12-week low = a wave is starting (phase *Rising*).
  * **COVID alert**: momentum fired *and* the level is at, or (Berlin) projected within three weeks to reach, "Right around the threshold". *Watch* = momentum only.
  * **Flu/RSV**: percentile high mark (default 75th, adjustable); CDC categories as CDC publishes them; "at CDC's minimum level" when the level sits at its floor.

## Methods project

`methods/` holds the research project that produced the conversion: pipeline (`methods/pipeline/`), checks (`methods/analysis/`), fitted outputs (`methods/data/`), and the full methods document with validation log (`methods/wastewater-threshold-handoff.md`). It needs pandas, numpy, scipy and `pdftotext`; its raw downloads are not committed. Refits: routine in late October 2027; check-in around April 2027 (season level, first real CDC restatement); CDC category cut-offs every August. A refit regenerates `pipeline/threshold_config.json` via `methods/pipeline/export_app_config.py`; the app never refits.

## Build

Requires Android Studio / SDK with platform 36, JDK 17+.

```
./gradlew :app:assembleDebug
./gradlew :app:assembleRelease   # needs keystore.properties (not committed)
adb install -r app/build/outputs/apk/release/app-release.apk
```

Run the pipeline locally (standard library only):

```
python3 pipeline/build_data.py --out data
```

## Data licence

RKI data is CC-BY 4.0; NYC and CDC data are public domain. This app is not affiliated with any of them.
