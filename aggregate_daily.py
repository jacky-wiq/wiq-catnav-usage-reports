import csv, json
from collections import defaultdict, Counter
import datetime

from config import (CSV_PATH, AU_PORTFOLIOS_PATH, AU_USERS_PATH, USAGE_BY_USER_PATH,
                    PSEUDO_USERS_PATH, DATA_PATH as OUT_PATH)

DOW_ORDER = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
CLICK_CAP = 150
PORTFOLIO_CLICK_CAP = 40  # smaller cap for the per-portfolio cross to keep output size sane
HEATMAP_MODULES = 6

rows = []
with open(CSV_PATH, newline="") as f:
    r = csv.DictReader(f)
    for row in r:
        rows.append(row)

# ---- portfolio canonicalization ----
# user_portfolio in the raw events is a mix of portfolio CATEGORY names and
# portfolio OWNER names (or occasionally a non-owner user's own name) for the
# same portfolio. The "CatNav Users and Portfolios" master sheet resolves this:
# https://docs.google.com/spreadsheets/d/1cRp90r5ziSHP9SRguGbz2gzznQSJ61oat41qGXLbqmw
_au_portfolios = json.load(open(AU_PORTFOLIOS_PATH))["values"]
_au_users = json.load(open(AU_USERS_PATH))["values"]

_owner_to_portfolio = {}
_portfolio_names = set()
for _row in _au_portfolios[1:]:
    if len(_row) < 2:
        continue
    _portfolio, _owner = _row[0].strip(), _row[1].strip()
    if _portfolio:
        _portfolio_names.add(_portfolio)
    if _owner:
        _owner_to_portfolio[_owner] = _portfolio

_user_to_portfolio = {}
for _row in _au_users[1:]:
    if len(_row) < 5:
        continue
    _user_name, _portfolio_name = _row[0].strip(), _row[4].strip()
    if _user_name and _portfolio_name:
        _user_to_portfolio[_user_name] = _portfolio_name

_portfolio_names_lower = {p.lower(): p for p in _portfolio_names}
_unmapped_portfolio_count = 0

def canonicalize_portfolio(raw):
    global _unmapped_portfolio_count
    raw = raw.strip()
    if not raw:
        return None
    if raw.lower() in _portfolio_names_lower:
        return _portfolio_names_lower[raw.lower()]
    if raw in _owner_to_portfolio:
        return _owner_to_portfolio[raw]
    if raw in _user_to_portfolio:
        return _user_to_portfolio[raw]
    _unmapped_portfolio_count += 1
    return None

dates = sorted(set(row["event_date"] for row in rows))
date_index = {d: i for i, d in enumerate(dates)}
n_dates = len(dates)
dow_of_date = [DOW_ORDER[datetime.date.fromisoformat(d).weekday()] for d in dates]

# ---- overall popularity (to fix module order + click cap) ----
click_total = Counter()
for row in rows:
    vt, vv = row["var_title"].strip(), row["var_value"].strip()
    if vt or vv:
        click_total[(vt or "(no title)", vv or "(no value)")] += 1

module_sessions_total = defaultdict(set)
for row in rows:
    mod = row["module"].strip()
    if mod:
        module_sessions_total[mod].add(row["session_id"])
modules = sorted(module_sessions_total.keys(), key=lambda m: -len(module_sessions_total[m]))
module_index = {m: i for i, m in enumerate(modules)}
heat_modules = modules[:HEATMAP_MODULES]

top_combos = [c for c, _ in click_total.most_common(CLICK_CAP)]
combo_index = {c: i for i, c in enumerate(top_combos)}
portfolio_top_combos = [c for c, _ in click_total.most_common(PORTFOLIO_CLICK_CAP)]
portfolio_combo_index = {c: i for i, c in enumerate(portfolio_top_combos)}

# ---- canonicalize Time Horizon / Level of Analysis values (merge case variants) ----
def canon_map(rows_filter):
    raw_counts = Counter()
    for row in rows:
        vt = row["var_title"].strip().lower()
        vv = row["var_value"].strip()
        if vt in rows_filter and vv:
            raw_counts[vv] += 1
    by_lower = defaultdict(Counter)
    for vv, c in raw_counts.items():
        by_lower[vv.lower()][vv] += c
    canon = {}
    display = {}
    for lower_key, variants in by_lower.items():
        best = variants.most_common(1)[0][0]
        display[lower_key] = best
        for variant in variants:
            canon[variant] = lower_key
    totals = Counter()
    for lower_key, variants in by_lower.items():
        totals[lower_key] = sum(variants.values())
    ordered = [display[k] for k, _ in totals.most_common()]
    return canon, ordered  # canon: raw var_value -> lowercased canonical key; ordered: display labels sorted desc

time_horizon_canon, time_horizon_values = canon_map({"time horizon", "time period"})
time_horizon_value_index = {v.lower(): i for i, v in enumerate(time_horizon_values)}

loa_canon, level_of_analysis_values = canon_map({"level of analysis"})
loa_value_index = {v.lower(): i for i, v in enumerate(level_of_analysis_values)}

# ---- alert opens (alert_learn_more events, excluding the close-button action) ----
alert_open_total = Counter()
for row in rows:
    if row["event"] == "alert_learn_more":
        vv = row["var_value"].strip()
        if "close-button" not in vv and "open_dialog_button" in vv:
            alert_open_total[row["var_title"].strip()] += 1
alert_types = [t for t, _ in alert_open_total.most_common()]
alert_type_index = {t: i for i, t in enumerate(alert_types)}

# ---- AI sales commentary (dedicated `ai_sales_commentary` event) ----
# Same detection condition used below for session_ai / intent bucketing: either the
# dedicated event fires, or (defensively) the var_value label appears on some other
# event. var_value distinguishes opening the assistant panel ("AI assistant", plus
# the one-off launch-day label "AI assistant BETA x") from actually generating a
# commentary ("Generate now") -- everything else collapses to "Opened".
ai_action_order = ["Opened", "Generated"]
ai_action_index = {a: i for i, a in enumerate(ai_action_order)}

_ai_module_total = Counter()
for row in rows:
    if row["event"] == "ai_sales_commentary" or row["var_value"].strip() == "AI assistant":
        mod_ = row["module"].strip()
        _ai_module_total[mod_ if mod_ else "General"] += 1
ai_modules = [m for m, _ in _ai_module_total.most_common()]
ai_module_index = {m: i for i, m in enumerate(ai_modules)}

# ---- per-date accumulators (overall) ----
events_by_date = [0] * n_dates
engaged_seconds_by_date = [0.0] * n_dates
sessions_by_date_set = [set() for _ in range(n_dates)]
module_sessions = [defaultdict(set) for _ in range(n_dates)]   # date -> module -> set(session_id)
module_events = [defaultdict(int) for _ in range(n_dates)]
module_engaged = [defaultdict(float) for _ in range(n_dates)]
session_engaged_by_date = [defaultdict(float) for _ in range(n_dates)]  # date -> session_id -> seconds
portfolio_engaged = [defaultdict(float) for _ in range(n_dates)]
portfolio_sessions = [defaultdict(set) for _ in range(n_dates)]
user_ids_by_date = [set() for _ in range(n_dates)]
click_counts = [defaultdict(int) for _ in range(n_dates)]  # date -> comboIdx -> count
other_click_count = [0] * n_dates
hour_counts = [defaultdict(int) for _ in range(n_dates)]   # date -> hour(0-23) -> events
alert_open_counts = [defaultdict(int) for _ in range(n_dates)]  # date -> alertTypeIdx -> opens
time_horizon_counts = [defaultdict(int) for _ in range(n_dates)]  # date -> valueIdx -> count
loa_counts = [defaultdict(int) for _ in range(n_dates)]           # date -> valueIdx -> count
ai_counts = [defaultdict(int) for _ in range(n_dates)]            # date -> actionIdx -> count
ai_module_counts = [defaultdict(int) for _ in range(n_dates)]     # date -> aiModuleIdx -> count

# ---- per-date, per-PORTFOLIO accumulators (for the portfolio drill-down) ----
portfolio_events_count = [defaultdict(int) for _ in range(n_dates)]           # date -> portfolio -> events
portfolio_user_ids = [defaultdict(set) for _ in range(n_dates)]               # date -> portfolio -> set(user_id)
portfolio_module_sessions = [defaultdict(lambda: defaultdict(set)) for _ in range(n_dates)]   # date -> portfolio -> module -> set(sid)
portfolio_module_events = [defaultdict(lambda: defaultdict(int)) for _ in range(n_dates)]
portfolio_module_engaged = [defaultdict(lambda: defaultdict(float)) for _ in range(n_dates)]
portfolio_alert_counts = [defaultdict(lambda: defaultdict(int)) for _ in range(n_dates)]
portfolio_horizon_counts = [defaultdict(lambda: defaultdict(int)) for _ in range(n_dates)]
portfolio_loa_counts = [defaultdict(lambda: defaultdict(int)) for _ in range(n_dates)]
portfolio_hour_counts = [defaultdict(lambda: defaultdict(int)) for _ in range(n_dates)]
portfolio_click_counts = [defaultdict(lambda: defaultdict(int)) for _ in range(n_dates)]
portfolio_ai_counts = [defaultdict(lambda: defaultdict(int)) for _ in range(n_dates)]         # date -> portfolio -> actionIdx -> count
portfolio_ai_module_counts = [defaultdict(lambda: defaultdict(int)) for _ in range(n_dates)]  # date -> portfolio -> aiModuleIdx -> count

# ---- global (whole-session, not per-date) features for intent bucketing ----
session_modules = defaultdict(set)
session_total_events = defaultdict(int)
session_total_engaged = defaultdict(float)
session_nav = defaultdict(int)
session_filter = defaultdict(int)
session_datatable = defaultdict(int)
session_ai = defaultdict(int)
session_alert = defaultdict(int)
session_user = {}                     # session_id -> user_id
session_portfolio = {}                # session_id -> canonical portfolio (first non-blank seen)
session_first_module_dt = {}          # session_id -> earliest event_dt string seen with a module
session_first_module = {}             # session_id -> module name
session_first_module_date_idx = {}    # session_id -> date_idx of that earliest module touch

for row in rows:
    di = date_index[row["event_date"]]
    sid = row["session_id"]
    mod = row["module"].strip()
    portfolio = canonicalize_portfolio(row["user_portfolio"]) or ""
    engaged = row["engagement_time_sec"]

    events_by_date[di] += 1
    sessions_by_date_set[di].add(sid)
    user_ids_by_date[di].add(row["user_id"])

    if mod:
        module_sessions[di][mod].add(sid)
        module_events[di][mod] += 1

    if engaged:
        val = float(engaged)
        engaged_seconds_by_date[di] += val
        session_engaged_by_date[di][sid] += val
        if mod:
            module_engaged[di][mod] += val
        if portfolio:
            portfolio_engaged[di][portfolio] += val

    if portfolio:
        portfolio_sessions[di][portfolio].add(sid)

    vt, vv = row["var_title"].strip(), row["var_value"].strip()
    if vt or vv:
        key = (vt or "(no title)", vv or "(no value)")
        if key in combo_index:
            click_counts[di][combo_index[key]] += 1
        else:
            other_click_count[di] += 1
    else:
        key = None

    vt_lower = vt.lower()
    if vt_lower in ("time horizon", "time period") and vv in time_horizon_canon:
        time_horizon_counts[di][time_horizon_value_index[time_horizon_canon[vv]]] += 1
    if vt_lower == "level of analysis" and vv in loa_canon:
        loa_counts[di][loa_value_index[loa_canon[vv]]] += 1

    is_alert_open = row["event"] == "alert_learn_more" and "close-button" not in vv and "open_dialog_button" in vv and vt in alert_type_index
    if is_alert_open:
        alert_open_counts[di][alert_type_index[vt]] += 1

    hour = int(row["event_dt"][11:13])
    hour_counts[di][hour] += 1

    if sid not in session_user:
        session_user[sid] = row["user_id"]
    if portfolio and sid not in session_portfolio:
        session_portfolio[sid] = portfolio
    if mod:
        dt = row["event_dt"]
        if sid not in session_first_module_dt or dt < session_first_module_dt[sid]:
            session_first_module_dt[sid] = dt
            session_first_module[sid] = mod
            session_first_module_date_idx[sid] = di

    ev = row["event"]
    session_total_events[sid] += 1
    if mod:
        session_modules[sid].add(mod)
    if engaged:
        session_total_engaged[sid] += float(engaged)
    if ev in ("main_navigation_click", "sub_navigation_click"):
        session_nav[sid] += 1
    if ev in ("filter_dropdown_click", "filter_radio_click", "filter_button_click", "toggle_click"):
        session_filter[sid] += 1
    if ev == "datatable_row_click":
        session_datatable[sid] += 1
    is_ai_commentary = ev == "ai_sales_commentary" or vv == "AI assistant"
    if is_ai_commentary:
        session_ai[sid] += 1
        ai_action_idx = ai_action_index["Generated" if vv == "Generate now" else "Opened"]
        ai_mod_idx = ai_module_index[mod if mod else "General"]
        ai_counts[di][ai_action_idx] += 1
        ai_module_counts[di][ai_mod_idx] += 1
    if ev == "alert_learn_more":
        session_alert[sid] += 1

    # ---- portfolio-crossed accumulation ----
    if portfolio:
        portfolio_events_count[di][portfolio] += 1
        portfolio_user_ids[di][portfolio].add(row["user_id"])
        if mod:
            portfolio_module_sessions[di][portfolio][mod].add(sid)
            portfolio_module_events[di][portfolio][mod] += 1
            if engaged:
                portfolio_module_engaged[di][portfolio][mod] += float(engaged)
        if is_alert_open:
            portfolio_alert_counts[di][portfolio][alert_type_index[vt]] += 1
        if is_ai_commentary:
            portfolio_ai_counts[di][portfolio][ai_action_idx] += 1
            portfolio_ai_module_counts[di][portfolio][ai_mod_idx] += 1
        if vt_lower in ("time horizon", "time period") and vv in time_horizon_canon:
            portfolio_horizon_counts[di][portfolio][time_horizon_value_index[time_horizon_canon[vv]]] += 1
        if vt_lower == "level of analysis" and vv in loa_canon:
            portfolio_loa_counts[di][portfolio][loa_value_index[loa_canon[vv]]] += 1
        portfolio_hour_counts[di][portfolio][hour] += 1
        if key is not None and key in portfolio_combo_index:
            portfolio_click_counts[di][portfolio][portfolio_combo_index[key]] += 1

# ---- canonical portfolio ordering (by total engaged time, desc) ----
_portfolio_total_engaged = Counter()
for di in range(n_dates):
    for p, sec in portfolio_engaged[di].items():
        _portfolio_total_engaged[p] += sec
_all_portfolios_seen = set()
for di in range(n_dates):
    _all_portfolios_seen.update(portfolio_sessions[di].keys())
portfolios = sorted(_all_portfolios_seen, key=lambda p: -_portfolio_total_engaged.get(p, 0))
portfolio_index = {p: i for i, p in enumerate(portfolios)}

# ---- real distinct-user roster per portfolio, from the official usage_by_user table ----
# (gcp-wow-wiq-kscope-prod.prod.usage_by_user) -- this table is keyed by real
# user_email/user_name/user_role together with portfolio_name, so it's the most
# reliable way to answer "who (by name) uses this portfolio". Independent of the
# daily event date range -- this table's own history (back to 2024) is used as-is,
# all-time. (See user_identity below for resolving fact_user_events.user_id itself,
# an anonymous GA client ID, to an email -- probabilistic, not this table's kind of
# authoritative join.)
_usage_by_user_rows = json.load(open(USAGE_BY_USER_PATH))
_roster_stats = defaultdict(lambda: defaultdict(lambda: {"weeks": 0, "days": 0, "last": "", "role": "", "name": ""}))
for _r in _usage_by_user_rows:
    _p = (_r.get("portfolio_name") or "").strip()
    _email = (_r.get("user_email") or "").strip()
    if not _p or not _email or _p not in portfolio_index:
        continue
    _stat = _roster_stats[_p][_email]
    _stat["weeks"] += 1
    _stat["days"] += int(_r.get("days_active") or 0)
    _stat["name"] = _r.get("user_name") or _email
    _stat["role"] = _r.get("user_role") or ""
    _week = _r.get("fiscal_week_end_dt") or ""
    if _week > _stat["last"]:
        _stat["last"] = _week

user_roster = [[] for _ in portfolios]  # aligned to portfolio_index; each entry: list of {name, email, role, weeks, days, last}
for _p, _users in _roster_stats.items():
    _pi = portfolio_index[_p]
    for _email, _stat in _users.items():
        user_roster[_pi].append({
            "name": _stat["name"], "email": _email, "role": _stat["role"],
            "weeks": _stat["weeks"], "days": _stat["days"], "last": _stat["last"],
        })
    user_roster[_pi].sort(key=lambda d: -d["weeks"])

# ---- probabilistic user_id -> email identity, from dim_pseudo_users ----
# NOT an authenticated login binding -- inferred behaviorally (e.g. from "View by:
# <name>" filter selections co-occurring with a given anonymous id) by the data
# team's own pipeline, with a confidence score = share of that id's identifying
# observations pointing to this email. Coverage is incomplete: ids with too little
# identifying activity in their history have no entry here at all.
_pseudo_users_rows = json.load(open(PSEUDO_USERS_PATH))
user_identity = {}
for _r in _pseudo_users_rows:
    _uid = _r.get("pseudo_user_id")
    _email = _r.get("user_email_inferred")
    if not _uid or not _email:
        continue
    user_identity[_uid] = {
        "name": _r.get("user_name") or "",
        "email": _email,
        "confidence": round(float(_r.get("user_email_confidence") or 0), 3),
        "matches": int(_r.get("user_email_matches") or 0),
    }

# ---- classify every session into one intent bucket ----
INTENT_BUCKETS = [
    "Bounce", "Multi-module review", "AI-assisted exploration", "Alert triage",
    "Deep-dive analysis", "Navigation / browsing", "Quick check", "Standard single-module use",
]
intent_index = {b: i for i, b in enumerate(INTENT_BUCKETS)}

def classify(sid):
    n_mod = len(session_modules[sid])
    te = session_total_events[sid]
    eng = session_total_engaged[sid]
    if n_mod == 0 and te <= 2:
        return "Bounce"
    if n_mod >= 4:
        return "Multi-module review"
    if session_ai[sid] > 0:
        return "AI-assisted exploration"
    if session_alert[sid] > 0:
        return "Alert triage"
    if eng >= 122 and (session_filter[sid] >= 3 or session_datatable[sid] >= 1):
        return "Deep-dive analysis"
    if te >= 3 and session_nav[sid] / te >= 0.5:
        return "Navigation / browsing"
    if eng < 29 or te <= 5:
        return "Quick check"
    return "Standard single-module use"

session_bucket = {sid: classify(sid) for sid in session_total_events}

# ---- new vs. returning: a user's first active calendar date is "new", any later date is "returning" ----
user_first_date_idx = {}
for di in range(n_dates):
    for sid in sessions_by_date_set[di]:
        u = session_user[sid]
        if u not in user_first_date_idx:
            user_first_date_idx[u] = di

new_returning_daily = []  # [dateIdx, isReturning(0/1), sessionCount]
portfolio_newret_counts = [defaultdict(lambda: [0, 0]) for _ in range(n_dates)]  # date -> portfolio -> [new, returning]
for di in range(n_dates):
    counts = [0, 0]
    for sid in sessions_by_date_set[di]:
        is_returning = 1 if di > user_first_date_idx[session_user[sid]] else 0
        counts[is_returning] += 1
        p = session_portfolio.get(sid)
        if p:
            portfolio_newret_counts[di][p][is_returning] += 1
    for is_returning, c in enumerate(counts):
        if c:
            new_returning_daily.append([di, is_returning, c])

# ---- session-length buckets, per date (+ per portfolio) ----
BUCKETS = [
    ("< 30s", 0, 30), ("30s - 1m", 30, 60), ("1 - 3m", 60, 180),
    ("3 - 5m", 180, 300), ("5 - 10m", 300, 600), ("10m+", 600, float("inf")),
]
bucket_labels = [b[0] for b in BUCKETS]
session_len_daily = []  # [dateIdx, bucketIdx, count]
portfolio_sessionlen_counts = [defaultdict(lambda: defaultdict(int)) for _ in range(n_dates)]  # date -> portfolio -> bucketIdx -> count
for di in range(n_dates):
    counts = [0] * len(BUCKETS)
    for sid, sec in session_engaged_by_date[di].items():
        for bi, (label, lo, hi) in enumerate(BUCKETS):
            if lo <= sec < hi:
                counts[bi] += 1
                p = session_portfolio.get(sid)
                if p:
                    portfolio_sessionlen_counts[di][p][bi] += 1
                break
    for bi, c in enumerate(counts):
        if c:
            session_len_daily.append([di, bi, c])

# ---- intent buckets, per date (+ per portfolio) ----
bucket_daily = []  # [dateIdx, bucketIdx, sessionCount, engagedMinutes]
portfolio_intent_counts = [defaultdict(lambda: defaultdict(lambda: [0, 0.0])) for _ in range(n_dates)]  # date -> portfolio -> bucketIdx -> [count, minutes]
for di in range(n_dates):
    counts = defaultdict(int)
    minutes = defaultdict(float)
    for sid in sessions_by_date_set[di]:
        b = session_bucket[sid]
        bi = intent_index[b]
        eng_min = session_engaged_by_date[di].get(sid, 0.0) / 60
        counts[bi] += 1
        minutes[bi] += eng_min
        p = session_portfolio.get(sid)
        if p:
            portfolio_intent_counts[di][p][bi][0] += 1
            portfolio_intent_counts[di][p][bi][1] += eng_min
    for bi in counts:
        bucket_daily.append([di, bi, counts[bi], round(minutes[bi], 2)])

# ---- entry module, per date (+ per portfolio) ----
entry_module_daily = []  # [dateIdx, moduleIdx, sessionCount] -- first module touched per session
entry_counts = [defaultdict(int) for _ in range(n_dates)]
portfolio_entry_counts = [defaultdict(lambda: defaultdict(int)) for _ in range(n_dates)]  # date -> portfolio -> moduleIdx -> count
for sid, mod in session_first_module.items():
    di = session_first_module_date_idx[sid]
    entry_counts[di][module_index[mod]] += 1
    p = session_portfolio.get(sid)
    if p:
        portfolio_entry_counts[di][p][module_index[mod]] += 1
for di in range(n_dates):
    for mi, c in entry_counts[di].items():
        entry_module_daily.append([di, mi, c])

# ---- flatten overall sparse structures ----
module_daily = []  # [dateIdx, moduleIdx, sessions, events, engagedMinutes]
for di in range(n_dates):
    for mod in modules:
        s = len(module_sessions[di].get(mod, ()))
        e = module_events[di].get(mod, 0)
        eng = round(module_engaged[di].get(mod, 0.0) / 60, 2)
        if s or e or eng:
            module_daily.append([di, module_index[mod], s, e, eng])

heat_daily = []  # [dateIdx, heatModuleIdx, sessions] for the top-6 heatmap modules only
for di in range(n_dates):
    for hi, mod in enumerate(heat_modules):
        s = len(module_sessions[di].get(mod, ()))
        if s:
            heat_daily.append([di, hi, s])

portfolio_daily = []  # [dateIdx, portfolioName, minutes, sessions]
for di in range(n_dates):
    for p, sec in portfolio_engaged[di].items():
        portfolio_daily.append([di, p, round(sec / 60, 2), len(portfolio_sessions[di].get(p, ()))])
    for p, sset in portfolio_sessions[di].items():
        if p not in portfolio_engaged[di]:
            portfolio_daily.append([di, p, 0, len(sset)])

click_daily = []  # [dateIdx, comboIdx, count]
for di in range(n_dates):
    for ci, c in click_counts[di].items():
        click_daily.append([di, ci, c])

user_daily = []  # [dateIdx, userId] sparse presence
for di in range(n_dates):
    for u in user_ids_by_date[di]:
        user_daily.append([di, u])

hour_daily = []  # [dateIdx, hour, events]
for di in range(n_dates):
    for hour_, c in hour_counts[di].items():
        hour_daily.append([di, hour_, c])

time_horizon_daily = []  # [dateIdx, valueIdx, count]
for di in range(n_dates):
    for vi, c in time_horizon_counts[di].items():
        time_horizon_daily.append([di, vi, c])

level_of_analysis_daily = []  # [dateIdx, valueIdx, count]
for di in range(n_dates):
    for vi, c in loa_counts[di].items():
        level_of_analysis_daily.append([di, vi, c])

alert_daily = []  # [dateIdx, alertTypeIdx, opens]
for di in range(n_dates):
    for ai, c in alert_open_counts[di].items():
        alert_daily.append([di, ai, c])

ai_daily = []  # [dateIdx, actionIdx, count]
for di in range(n_dates):
    for act_idx, c in ai_counts[di].items():
        ai_daily.append([di, act_idx, c])

ai_module_daily = []  # [dateIdx, aiModuleIdx, count]
for di in range(n_dates):
    for mi, c in ai_module_counts[di].items():
        ai_module_daily.append([di, mi, c])

# ---- flatten portfolio-crossed structures ----
portfolio_module_daily = []  # [dateIdx, portfolioIdx, moduleIdx, sessions, events, engagedMinutes]
for di in range(n_dates):
    for p, mod_map in portfolio_module_sessions[di].items():
        pi = portfolio_index[p]
        for mod, sset in mod_map.items():
            e = portfolio_module_events[di][p].get(mod, 0)
            eng = round(portfolio_module_engaged[di][p].get(mod, 0.0) / 60, 2)
            portfolio_module_daily.append([di, pi, module_index[mod], len(sset), e, eng])

portfolio_entry_daily = []  # [dateIdx, portfolioIdx, moduleIdx, sessionCount]
for di in range(n_dates):
    for p, mod_map in portfolio_entry_counts[di].items():
        pi = portfolio_index[p]
        for mi, c in mod_map.items():
            portfolio_entry_daily.append([di, pi, mi, c])

portfolio_alert_daily = []  # [dateIdx, portfolioIdx, alertTypeIdx, opens]
for di in range(n_dates):
    for p, a_map in portfolio_alert_counts[di].items():
        pi = portfolio_index[p]
        for ai, c in a_map.items():
            portfolio_alert_daily.append([di, pi, ai, c])

portfolio_ai_daily = []  # [dateIdx, portfolioIdx, actionIdx, count]
for di in range(n_dates):
    for p, a_map in portfolio_ai_counts[di].items():
        pi = portfolio_index[p]
        for act_idx, c in a_map.items():
            portfolio_ai_daily.append([di, pi, act_idx, c])

portfolio_ai_module_daily = []  # [dateIdx, portfolioIdx, aiModuleIdx, count]
for di in range(n_dates):
    for p, m_map in portfolio_ai_module_counts[di].items():
        pi = portfolio_index[p]
        for mi, c in m_map.items():
            portfolio_ai_module_daily.append([di, pi, mi, c])

portfolio_horizon_daily = []  # [dateIdx, portfolioIdx, valueIdx, count]
for di in range(n_dates):
    for p, v_map in portfolio_horizon_counts[di].items():
        pi = portfolio_index[p]
        for vi, c in v_map.items():
            portfolio_horizon_daily.append([di, pi, vi, c])

portfolio_loa_daily = []  # [dateIdx, portfolioIdx, valueIdx, count]
for di in range(n_dates):
    for p, v_map in portfolio_loa_counts[di].items():
        pi = portfolio_index[p]
        for vi, c in v_map.items():
            portfolio_loa_daily.append([di, pi, vi, c])

portfolio_hour_daily = []  # [dateIdx, portfolioIdx, hour, events]
for di in range(n_dates):
    for p, h_map in portfolio_hour_counts[di].items():
        pi = portfolio_index[p]
        for h, c in h_map.items():
            portfolio_hour_daily.append([di, pi, h, c])

portfolio_click_daily = []  # [dateIdx, portfolioIdx, comboIdx, count]
for di in range(n_dates):
    for p, c_map in portfolio_click_counts[di].items():
        pi = portfolio_index[p]
        for ci, c in c_map.items():
            portfolio_click_daily.append([di, pi, ci, c])

portfolio_sessionlen_daily = []  # [dateIdx, portfolioIdx, bucketIdx, count]
for di in range(n_dates):
    for p, b_map in portfolio_sessionlen_counts[di].items():
        pi = portfolio_index[p]
        for bi, c in b_map.items():
            portfolio_sessionlen_daily.append([di, pi, bi, c])

portfolio_newret_daily = []  # [dateIdx, portfolioIdx, isReturning, count]
for di in range(n_dates):
    for p, counts in portfolio_newret_counts[di].items():
        pi = portfolio_index[p]
        for is_returning, c in enumerate(counts):
            if c:
                portfolio_newret_daily.append([di, pi, is_returning, c])

portfolio_intent_daily = []  # [dateIdx, portfolioIdx, bucketIdx, sessionCount, engagedMinutes]
for di in range(n_dates):
    for p, b_map in portfolio_intent_counts[di].items():
        pi = portfolio_index[p]
        for bi, (c, mins) in b_map.items():
            portfolio_intent_daily.append([di, pi, bi, c, round(mins, 2)])

portfolio_events_daily = []  # [dateIdx, portfolioIdx, events]
for di in range(n_dates):
    for p, c in portfolio_events_count[di].items():
        portfolio_events_daily.append([di, portfolio_index[p], c])

portfolio_user_daily = []  # [dateIdx, portfolioIdx, userId] sparse presence
for di in range(n_dates):
    for p, uset in portfolio_user_ids[di].items():
        pi = portfolio_index[p]
        for u in uset:
            portfolio_user_daily.append([di, pi, u])

out = {
    "dates": dates,
    "dow_of_date": dow_of_date,
    "modules": modules,
    "heat_modules": heat_modules,
    "combos": [{"title": t, "value": v} for t, v in top_combos],
    "portfolio_combos": [{"title": t, "value": v} for t, v in portfolio_top_combos],
    "bucket_labels": bucket_labels,
    "portfolios": portfolios,
    "events_by_date": events_by_date,
    "engaged_minutes_by_date": [round(s / 60, 2) for s in engaged_seconds_by_date],
    "sessions_by_date": [len(s) for s in sessions_by_date_set],
    "module_daily": module_daily,
    "heat_daily": heat_daily,
    "portfolio_daily": portfolio_daily,
    "click_daily": click_daily,
    "other_click_by_date": other_click_count,
    "session_len_daily": session_len_daily,
    "user_daily": user_daily,
    "intent_buckets": INTENT_BUCKETS,
    "bucket_daily": bucket_daily,
    "new_returning_daily": new_returning_daily,
    "hour_daily": hour_daily,
    "time_horizon_values": time_horizon_values,
    "time_horizon_daily": time_horizon_daily,
    "level_of_analysis_values": level_of_analysis_values,
    "level_of_analysis_daily": level_of_analysis_daily,
    "entry_module_daily": entry_module_daily,
    "alert_types": alert_types,
    "alert_daily": alert_daily,
    "ai_actions": ai_action_order,
    "ai_modules": ai_modules,
    "ai_daily": ai_daily,
    "ai_module_daily": ai_module_daily,
    # portfolio-crossed (for the per-portfolio drill-down view)
    "portfolio_module_daily": portfolio_module_daily,
    "portfolio_entry_daily": portfolio_entry_daily,
    "portfolio_alert_daily": portfolio_alert_daily,
    "portfolio_ai_daily": portfolio_ai_daily,
    "portfolio_ai_module_daily": portfolio_ai_module_daily,
    "portfolio_horizon_daily": portfolio_horizon_daily,
    "portfolio_loa_daily": portfolio_loa_daily,
    "portfolio_hour_daily": portfolio_hour_daily,
    "portfolio_click_daily": portfolio_click_daily,
    "portfolio_sessionlen_daily": portfolio_sessionlen_daily,
    "portfolio_newret_daily": portfolio_newret_daily,
    "portfolio_intent_daily": portfolio_intent_daily,
    "portfolio_events_daily": portfolio_events_daily,
    "portfolio_user_daily": portfolio_user_daily,
    "user_roster": user_roster,
    "user_identity": user_identity,
}

with open(OUT_PATH, "w") as f:
    json.dump(out, f, separators=(",", ":"))

import os
print("dates:", n_dates)
print("modules:", modules)
print("portfolios:", len(portfolios))
print("module_daily rows:", len(module_daily))
print("heat_daily rows:", len(heat_daily))
print("portfolio_daily rows:", len(portfolio_daily))
print("click_daily rows:", len(click_daily))
print("session_len_daily rows:", len(session_len_daily))
print("user_daily rows:", len(user_daily))
print("bucket_daily rows:", len(bucket_daily))
from collections import Counter as _C
print("bucket totals:", _C(session_bucket.values()).most_common())
print("new_returning_daily rows:", len(new_returning_daily))
print("hour_daily rows:", len(hour_daily))
print("time_horizon_values:", time_horizon_values)
print("time_horizon_daily rows:", len(time_horizon_daily))
print("level_of_analysis_values:", level_of_analysis_values)
print("level_of_analysis_daily rows:", len(level_of_analysis_daily))
print("entry_module_daily rows:", len(entry_module_daily))
print("entry module totals:", _C(session_first_module.values()).most_common())
print("alert_types:", alert_types)
print("alert_daily rows:", len(alert_daily))
print("alert open totals:", alert_open_total.most_common())
_ai_action_totals = Counter()
for _row in ai_daily:
    _ai_action_totals[ai_action_order[_row[1]]] += _row[2]
print("ai_modules:", ai_modules)
print("ai_daily rows:", len(ai_daily), "totals:", _ai_action_totals.most_common())
print("ai_module_daily rows:", len(ai_module_daily))
print("portfolio_ai_daily rows:", len(portfolio_ai_daily))
print("portfolio_ai_module_daily rows:", len(portfolio_ai_module_daily))
print("portfolio canonicalization: unmapped rows =", _unmapped_portfolio_count, "/ canonical portfolios =", len(portfolios), "of", len(_portfolio_names))
print("--- portfolio-crossed sizes ---")
print("portfolio_module_daily rows:", len(portfolio_module_daily))
print("portfolio_entry_daily rows:", len(portfolio_entry_daily))
print("portfolio_alert_daily rows:", len(portfolio_alert_daily))
print("portfolio_horizon_daily rows:", len(portfolio_horizon_daily))
print("portfolio_loa_daily rows:", len(portfolio_loa_daily))
print("portfolio_hour_daily rows:", len(portfolio_hour_daily))
print("portfolio_click_daily rows:", len(portfolio_click_daily))
print("portfolio_sessionlen_daily rows:", len(portfolio_sessionlen_daily))
print("portfolio_newret_daily rows:", len(portfolio_newret_daily))
print("portfolio_intent_daily rows:", len(portfolio_intent_daily))
print("portfolio_events_daily rows:", len(portfolio_events_daily))
print("portfolio_user_daily rows:", len(portfolio_user_daily))
print("user_roster: portfolios with users =", sum(1 for r in user_roster if r), "/ total roster entries =", sum(len(r) for r in user_roster))
_all_user_ids = set().union(*user_ids_by_date) if user_ids_by_date else set()
print("user_identity: {}/{} distinct user_ids resolved to an email ({:.0%})".format(
    len(user_identity), len(_all_user_ids),
    len(user_identity) / len(_all_user_ids) if _all_user_ids else 0))
_bn = portfolio_index.get("BABY NEEDS")
if _bn is not None:
    print("sample roster (BABY NEEDS):", user_roster[_bn][:5])
print("file size bytes:", os.path.getsize(OUT_PATH))
