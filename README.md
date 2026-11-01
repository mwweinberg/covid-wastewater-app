# Wastewater Watch

Android app that shows SARS-CoV-2 (plus influenza A/B and RSV) wastewater levels for Berlin, New York City and Washington DC, with a home-screen widget, trend detection and a "wave starting" alert.

## How it works

* `pipeline/build_data.py` runs twice a day in GitHub Actions. It slices three public feeds into small per-city JSON files under `data/`:
  * **Berlin**: Robert Koch-Institut [AMELAG](https://github.com/robert-koch-institut/Abwassersurveillance_AMELAG) per-site data, blended across the Ruhleben, Waßmannsdorf and Schönerlinde plants weighted by population served.
  * **New York City**: [NYC Open Data f7dc-2q9f](https://data.cityofnewyork.us/d/f7dc-2q9f), 14 DEP plants, population-weighted; flu/RSV and the official category come from CDC.
  * **Washington DC**: CDC [Wastewater Viral Activity Level](https://data.cdc.gov/d/atcp-73re) for the Blue Plains plant (DC plus Maryland and Virginia suburbs).
* The app (`app/`) fetches those files, caches them, and does all analysis on-device:
  * **Level**: percentile of the site's own full history. Raw copies/L are not comparable across cities or labs.
  * **Momentum**: three consecutive weekly rises, at least +10 % per week, and 3x the trailing 12-week low = a wave is starting.
  * **Alert**: momentum fired *and* the level is above, or projected within three weeks to cross, the high mark (default: 75th percentile, adjustable).

## Build

Requires Android Studio / SDK with platform 36, JDK 17+.

```
./gradlew :app:assembleDebug
./gradlew :app:assembleRelease   # needs keystore.properties (not committed)
adb install -r app/build/outputs/apk/release/app-release.apk
```

Run the pipeline locally:

```
python3 pipeline/build_data.py --out data
```

## Data licence

RKI data is CC-BY 4.0; NYC and CDC data are public domain. This app is not affiliated with any of them.
