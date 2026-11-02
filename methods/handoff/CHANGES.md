# Changes to the handoff

## 2026-11-02 (after the maintainer's review)

Replace `wastewater_threshold.py` with the new version. `threshold_config.json` is unchanged. `HANDOFF.md` sections 2.1–2.2 are updated.

1. **NYC trend includes the CDC fill-in weeks** (your point A, solved differently).
   - `_trend()` no longer skips provisional weeks, and `readout()` returns a new `trend_provisional`; mark the trend as provisional, like the reading.
   - *Why not the newest six measured weeks:* tested on past data, the trend NYC's own data later showed for the same week:

     | CDC fill-in weeks | Measured weeks only | Including fill-in |
     |---|---|---|
     | 1 | agrees 71%, backwards 0.9% | 96%, 0% |
     | 4 (typical) | 40%, 13.9% | 92%, 0% |

     The measured-weeks trend describes the situation 4–8 weeks ago.
   - *Today:* NYC shows "rising" (provisional).
2. **Phase badge and colour for COVID** (your point B, agreed with one addition).
   - Keep the badge, the high-mark slider (for phase logic, flu and RSV) and drop the 100k reference-line toggle.
   - *Addition:* for COVID, hide the badge when the phase is **Quiet**, and take the card and widget colour from the **category**. Otherwise a green "Quiet" card sits next to "Well above the threshold": NYC 49 of 165 weeks, DC 12 of 96, Berlin 8 of 109.
   - Rising, Peaking and Declining show as before. Badge-vs-trend contradictions are rare (at most one week per city).
