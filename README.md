# Daily Stock & Sector Ranking Dashboard — Phase 1

Implements the Phase 1 scope from `phase1_build_handoff.md`: a daily-use
dashboard built on a validated momentum/relative-strength model, with
append-only historical storage from day one.

## Architecture

```
ScoringEngine          (scoring_engine.py — abstract interface)
    ↓
MomentumModel           (momentum_model.py — only implementation today)
    ↓
Scores
```

The dashboard only ever reads from the storage layer; the storage layer
only ever receives output from a `ScoringEngine`. Nothing here is more
abstract than that on purpose — see the handoff doc for why.

## Files

| File | Role |
|---|---|
| `config.py` | Stock universe, sector mapping, benchmark/sector ETFs |
| `data_fetch.py` | Daily price pull (Yahoo Finance via `yfinance`) |
| `features.py` | Feature engineering (returns, RSI, trend R², vol, relative strength) |
| `scoring_engine.py` | `ScoringEngine` abstract interface |
| `momentum_model.py` | `MomentumModel`, the first `ScoringEngine` implementation |
| `storage.py` | Append-only historical Parquet/CSV store, matching the handoff schema exactly |
| `run_daily.py` | The script to run once per trading day: fetch → features → score → store |
| `dashboard.py` | Streamlit UI: global rankings, sector rankings, sector drill-down |
| `decision_journal.py` | Phase 2 decision log + Phase 3 batch outcome review |

## Running it

```bash
pip install -r requirements.txt

# Once per trading day:
python run_daily.py

# View the dashboard:
streamlit run dashboard.py
```

The first run will only have one day of history, which is enough for the
Global/Sector Rankings tabs (current-day scores don't need history) but
not for forward-return analysis — that accumulates as you keep running
`run_daily.py` each trading day.

## Running it fully automatically (recommended — zero daily friction)

The lowest-friction option isn't Colab at all — it's **GitHub Actions**, which
runs `run_daily.py` on a schedule in the cloud, with no machine of yours
involved and nothing for you to open each day. Colab can do this too, but
only with a paid Colab Pro scheduler; GitHub Actions does it for free.

**One-time setup (~5 minutes):**

1. Create a new GitHub repo (public or private — private is fine, this
   doesn't need to be public).
2. Push everything in this folder to it, including the `.github/workflows/`
   folder — that's what GitHub reads to know to run this on a schedule.
3. That's it. No secrets or tokens to add — the workflow's built-in
   permission to commit back to the repo is enough.

**What happens after that:**

- Every weekday at 21:30 UTC (~30-90 min after the US close, depending on
  daylight saving), GitHub spins up a fresh runner, installs dependencies,
  runs `run_daily.py`, and commits the new day's rows in `data/` back to
  the repo automatically.
- You can also trigger a run manually any time from the repo's **Actions**
  tab → "Daily Stock & Sector Scoring" → **Run workflow** — useful for the
  first run, or to backfill a missed day.
- Your historical data lives in the repo's `data/` folder and grows by
  itself, day after day, with zero action from you.

**To view the dashboard:** since the data now lives in your GitHub repo,
point the Colab notebook at it instead of Drive — clone the repo in a Colab
cell (`!git clone https://github.com/<you>/<repo>.git`) and set
`STOCK_DASH_DATA_DIR` to the cloned repo's `data/` folder, then run the
dashboard-view cells as before. Or, on a machine with Python installed,
`git pull` and run `streamlit run dashboard.py` for the full UI.

**Adjusting the schedule:** edit the `cron:` line in
`.github/workflows/daily.yml` — `cron` syntax is `minute hour day month weekday`,
always in UTC.



This was built and smoke-tested with synthetic price data, because this
sandbox's network allowlist doesn't include Yahoo Finance's API domains.
`data_fetch.py` itself is untouched by that — it'll pull real data the
first time you run it somewhere with normal internet access. Every other
module (features, scoring, storage, dashboard) has been exercised
end-to-end against a synthetic universe and works as written.

## Decisions made while implementing (beyond what the handoff doc fixed)

- **Decision Journal Outcome-field sequencing** (handoff doc flagged this
  as needing an explicit choice): resolved as **Phase 3 batch review**,
  not a Phase 2 daily task. See the docstring at the top of
  `decision_journal.py` for the reasoning — short version: an outcome
  usually isn't knowable same-day, and the forward-return data needed to
  compute it is already accumulating automatically in the historical
  store, so there's no reason to make Phase 2 heavier than "log the
  decision."
- **MomentumModel feature weights** are a reasonable starting composite
  consistent with the project's momentum/relative-strength framing, not
  a transcription of AION's exact internal weights (those weren't
  included in this handoff doc). If the AION reverse-engineering report
  has more precise validated weights, they drop into
  `FEATURE_WEIGHTS` in `momentum_model.py` — nothing else needs to change.
- **Starter universe** (`config.py`) is ~110 liquid large-caps across 11
  sectors — enough to make every dashboard view meaningful without
  pretending to be a full market census. Expand it freely.

## Next steps (per the handoff doc)

1. Run `python run_daily.py` somewhere with real network access to start
   accumulating real history.
2. Use the dashboard every trading day for two weeks (Phase 2) — no new
   features unless something is actually broken.
3. Use `decision_journal.log_decision(...)` daily during Phase 2.
4. At the two-week mark, move to Phase 3: run
   `decision_journal.batch_review()` to propose Outcomes, then let cited
   journal evidence (not vibes) drive whatever comes next.
