# CatNav Compass + CatNav Adoption

Two internal usage reports for the CatNav Category Manager toolset, rebuilt from live data by one script.

| Page | Answers | Built by |
|---|---|---|
| **CatNav Compass** | *How* CatNav is used: sessions, modules, alerts, AI commentary, intent, engagement, by portfolio and date range | `aggregate_daily.py` → `catnav_compass_template.html` |
| **CatNav Adoption** | *Who* in the Commercial Team uses it: % of CMs/ACMs ever / recently active, lapsed, never, fresh vs other categories | `adoption.py` → `catnav_adoption_template.html` |

Both are single, self-contained HTML files (data embedded, no server, no API calls), so they open in any browser.

> **Personal data.** Both pages and every raw pull name individual people (emails, usage, phone numbers in the roster sheet). Keep them inside Woolworths/WIQ access controls. Never commit data files (`.gitignore` enforces this) and never put the pages in a public bucket or link.

---

## Running it

### Option A: scheduled (recommended)
A Cloud Run job runs `refresh.py` every **Monday 09:00 Sydney** and uploads both pages to a private GCS bucket. Set it up once with [`deploy/deploy.sh`](deploy/deploy.sh); see that file for the exact steps. After that, nobody needs to log in each week.

Viewers (members of the viewer group) open:
`https://storage.cloud.google.com/<BUCKET>/catnav_compass.html` and `.../catnav_adoption.html`

### Option B: by hand (laptop / devcontainer)
```bash
gcloud auth login            # BigQuery, via the `bq` CLI
gws auth login ...           # Google Sheets, via the `gws` CLI (see ../reauth.sh for scopes)
python3 refresh.py
```
Writes `catnav_compass_output.html` + `catnav_adoption_output.html` here, and copies them to `/workspace/catnav_compass.html` / `/workspace/catnav_adoption.html`. Then publish or share them however you like (e.g. as claude.ai artifacts: ask Claude Code to "publish the CatNav pages").

**`Reauthentication failed`** from `bq` means your gcloud login has expired. Re-run `gcloud auth login`.

### Configuration (environment variables, all optional)
| Variable | Default | Purpose |
|---|---|---|
| `CATNAV_BACKEND` | `cli` | `cli` = `bq`/`gws` on your own login; `api` = Google client libraries on the service account (Cloud Run sets this) |
| `CATNAV_DATA_DIR` | this folder | where raw pulls are written |
| `CATNAV_OUTPUT_DIR` | this folder | where the two pages are written |
| `CATNAV_SHARE_COPY_DIR` | `/workspace` | extra copy of each page; empty to disable |
| `CATNAV_GCS_BUCKET` | *(none)* | upload pages here after each run |
| `CATNAV_BQ_PROJECT` | `gcp-wow-wiq-kscope-prod` | BigQuery project holding the `prod` dataset |

The `api` backend needs `pip install --require-hashes -r requirements.txt`. The `cli` backend uses only the standard library.

---

## Data sources

| Source | What | Used for |
|---|---|---|
| BQ `prod.fact_user_events` | Raw GA-style click/session events (`user_id` is a pseudonymous client ID) | All of Compass |
| BQ `prod.dim_pseudo_users` | **Probabilistic** `user_id` → name/email inference (not a login binding). About 30% of ids never resolve; that's expected | Compass "Unique users, identified" |
| BQ `prod.usage_by_user` | Weekly login activity per real user email + role + portfolio, since May 2024 | Compass "Who's using this portfolio"; all of Adoption |
| Sheet **CatNav Users and Portfolios** (`1cRp90r5…`) | `AU Portfolios` + `AU Users` tabs: the master portfolio ↔ owner mapping | Cleaning `user_portfolio` in Compass |
| Sheet **Commercial Team directory** (`1EVhLqKY…`) | Every CM/ACM by category section | Adoption roster |

**Don't use** the GCS export `gs://ga_user_tracking_catnav/fact_user_events`. It's a periodic copy that has silently stalled for weeks before. Always query BigQuery directly (the script does).

BigQuery data lags real time by roughly 4 days. That's normal upstream processing delay, not a bug.

---

## Definitions & known problems

### Adoption page
- **Roster**: every named row in the Commercial Team sheet whose Title is CM (incl. `CM: Mon-Wed`/`Wed-Fri`) or ACM (incl. `ACM/CA`). `TBC` rows are skipped. Two people hold both roles and count once (`CM+ACM`).
- **Ever adopted**: the person's email has any row in `usage_by_user`.
- **Active**: active in any week ending on or after *(latest week in the data − 90 days)*. The latest week can be the current, part-complete week.
- **Lapsed** = adopted but not active. **Never** = no rows at all.
- **Fresh** = everyone from the **"Seafood 1"** section to the end of the sheet (Seafood, Meat incl. Poultry, Fruit & Veg). Uses strict section boundaries on purpose: an earlier keyword-matching version wrongly counted Deli smallgoods, Deli salads, Frozen veg/fish and Ice cream (40 instead of 32 people). If the sheet is reordered, check `FRESH_FIRST_SECTION` in `adoption.py`.
- **Leave notes** (mat leave etc.) are hand-maintained in `leave_notes.json` (`{"Person Name": "mat leave"}`). It is git-ignored because it's HR information. Locally it sits next to the data files; for the Cloud Run job, upload it to `gs://<BUCKET>/config/leave_notes.json`. Update it when people go on or come back from leave. If the file is missing, the page still builds, just without the notes.
- Matching is by email, so someone using a different email in CatNav than in the sheet shows as "never". Check this before chasing a "never" person.

### Compass page
- **Portfolio cleaning**: `fact_user_events.user_portfolio` mixes category names ("Beef") and the owner's personal name for the same portfolio. `canonicalize_portfolio()` maps both to the real portfolio via the Users & Portfolios sheet. About 0.2% of rows ("Total Supermarket" etc.) are unmapped and dropped. If that sheet's columns change, update this function.
- **Two session counts per portfolio, both correct**: about 13% of sessions touch more than one portfolio. KPIs count a session under *every* portfolio it touched. Intent/entry-point/session-length charts count it under its *first* portfolio only. So the numbers legitimately differ.
- **Intent buckets** are rule-based, applied in priority order: Bounce → Multi-module (4+ modules) → AI-assisted → Alert triage → Deep-dive (≥122s and heavy filtering) → Navigation → Quick check (<29s or ≤5 events) → Standard.
- **Alerts**: only *opens* are tracked (the `alert_learn_more` event). There's no "alert shown" event, so true open *rates* can't be calculated.
- **AI commentary**: `ai_sales_commentary` event, live since 13 Apr 2026. `var_value = "Generate now"` = generated; anything else = opened the panel.
- Coverage is **AU only**. NZ lives in a separate project (`gcp-wow-wiq-036-prod`) and isn't included.
- There's also an official weekly login-stats sheet, "Daily Usage Stats (CatNav) – New" (`1rYhBYL-…`), maintained separately. Use it to cross-check headline user counts.

### Guard rails in the code
- Queries fail loudly if they hit their row cap (`FACT_MAX_ROWS` in `refresh.py`) rather than silently building on truncated data. `fact_user_events` grows by about 35k rows a month, so raise the cap when needed.
- Tiny run-to-run differences in the second decimal of some minute totals are floating-point ordering, not data changes.

---

## Changing things
- **Chart/layout changes**: edit the `*_template.html` files, never the `*_output.html` files (those are overwritten every run).
- **New metrics**: add the aggregation in `aggregate_daily.py` (Compass) or `adoption.py` (Adoption), then render it in the template.
- **New data source**: add the fetch to `sources.py` so it works on both backends, and call it from `refresh.py`.
- Test locally with `python3 refresh.py` before pushing; the Cloud Run job picks up changes on the next `gcloud run jobs deploy`.

## Ownership checklist (when handing over)
- [ ] New owner has access to this git repo
- [ ] New owner has BigQuery read on the three `prod` tables (for local runs / debugging)
- [ ] New owner can view both Google Sheets
- [ ] New owner added to the GCP project hosting the job, and to the viewer group
- [ ] Any claude.ai artifact links that were shared: either give the new owner edit access (Share menu), or point people at the bucket URLs instead
