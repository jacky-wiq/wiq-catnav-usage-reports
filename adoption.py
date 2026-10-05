"""Rebuild the CatNav Adoption page (Commercial Team CMs/ACMs vs CatNav logins).

Inputs (paths from config.py):
  - commercial_team_raw.txt : Commercial Team directory sheet values
                              (pulled fresh by this script unless --no-pull is passed)
  - usage_by_user_raw.json  : prod.usage_by_user extract written by refresh.py
Output:
  - catnav_adoption_output.html (refresh.py copies/uploads it afterwards)

Definitions (kept identical to the original 18 Sep 2026 page):
  - Adopted  = any row in usage_by_user for the person's email, ever
  - Active   = any week ending on/after (latest week in data - 90 days)
  - Lapsed   = adopted but not active
  - Fresh    = CMs/ACMs listed from the "Seafood 1" section to the end of the sheet
               (Seafood, Meat incl. Poultry, Fruit & Veg). Strict section boundaries —
               keyword matching wrongly pulled in Deli smallgoods, Deli salads, Frozen veg
               and Ice cream.
"""
import html
import json
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import config
import sources
from roster_lib import load_sheet_rows, parse_roster

SHEET_RANGE = "Commercial Team!A1:I450"
SHEET_PATH = config.COMMERCIAL_TEAM_PATH
USAGE_PATH = config.USAGE_BY_USER_PATH
TEMPLATE_PATH = config.ADOPTION_TEMPLATE_PATH
OUTPUT_PATH = config.ADOPTION_OUTPUT_PATH
FRESH_FIRST_SECTION = "Seafood 1"



def load_leave_notes():
    """Hand-maintained {name: note} leave annotations (e.g. "mat leave").

    Leave status is HR information, so it lives in git-ignored leave_notes.json in the
    data dir (refresh.py fetches it from the private bucket on Cloud Run), never in code.
    Missing file = no annotations; the page still builds.
    """
    try:
        with open(config.LEAVE_NOTES_PATH) as f:
            notes = json.load(f)
    except FileNotFoundError:
        print(f"No {config.LEAVE_NOTES_PATH} — building without leave annotations")
        return {}
    if not isinstance(notes, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in notes.items()):
        raise SystemExit(f"{config.LEAVE_NOTES_PATH} must be a JSON object of {{\"Person Name\": \"note\"}} strings.")
    return notes


def pull_sheet():
    values = sources.sheet_values(config.COMMERCIAL_TEAM_SHEET_ID, SHEET_RANGE)
    with open(SHEET_PATH, "w") as f:
        json.dump(values, f)


def today_sydney():
    """Today's date in Sydney — Cloud Run runs in UTC, which is still 'yesterday' on Monday morning."""
    try:
        return datetime.now(ZoneInfo("Australia/Sydney")).date()
    except ZoneInfoNotFoundError:
        return date.today()


def fmt_date(iso):
    d = date.fromisoformat(iso)
    return f"{d.day} {d.strftime('%b %Y')}"


def pct(n, d, places=1):
    return f"{n / d * 100:.{places}f}%" if d else "0%"


def short_pct(n, d):
    """Compact percentage for bar labels: no trailing .0."""
    s = pct(n, d)
    return s.replace(".0%", "%")


def counts(people):
    c = {"fresh": 0, "lapsed": 0, "never": 0}
    for p in people:
        c[p["status"]] += 1
    c["n"] = len(people)
    c["ever"] = c["fresh"] + c["lapsed"]
    return c


def badge(role):
    if role == "CM+ACM":
        return '<span class="badge badge-cm" style="margin-right:2px">CM</span><span class="badge badge-acm">ACM</span>'
    cls = "badge-cm" if role == "CM" else "badge-acm"
    return f'<span class="badge {cls}">{role}</span>'


def esc(s):
    return html.escape(s, quote=True)


def build():
    LEAVE_NOTES = load_leave_notes()
    rows = load_sheet_rows(SHEET_PATH)
    roster = parse_roster(rows)
    fresh_start = next(i for i, r in enumerate(rows) if r and r[0].strip().startswith(FRESH_FIRST_SECTION))
    fresh_keys = set(parse_roster(rows[fresh_start:]))

    with open(USAGE_PATH) as f:
        usage = json.load(f)
    max_week = max(r["fiscal_week_end_dt"] for r in usage)
    cutoff = (date.fromisoformat(max_week) - timedelta(days=90)).isoformat()

    stats = defaultdict(lambda: {"weeks": 0, "days": 0, "last": ""})
    for r in usage:
        email = (r.get("user_email") or "").lower()
        if not email:
            continue
        s = stats[email]
        s["weeks"] += 1
        s["days"] += int(r.get("days_active") or 0)
        s["last"] = max(s["last"], r["fiscal_week_end_dt"])

    people = []
    for key, p in roster.items():
        s = stats.get(key)
        status = "never" if not s else ("fresh" if s["last"] >= cutoff else "lapsed")
        people.append({**p, "key": key, "status": status, "is_fresh": key in fresh_keys,
                       **(s or {"weeks": 0, "days": 0, "last": ""})})

    has_role = lambda p, role: role in p["role"].split("+")
    all_c = counts(people)
    cm_c = counts([p for p in people if has_role(p, "CM")])
    acm_c = counts([p for p in people if has_role(p, "ACM")])
    n_both = sum(1 for p in people if p["role"] == "CM+ACM")
    fresh_people = [p for p in people if p["is_fresh"]]
    other_people = [p for p in people if not p["is_fresh"]]

    print(f"Roster {all_c['n']} ({cm_c['n']} CMs, {acm_c['n']} ACMs, {n_both} both); usage through {max_week}; cutoff {cutoff}")
    print(f"Ever {all_c['ever']} ({pct(all_c['ever'], all_c['n'])}), active {all_c['fresh']}, lapsed {all_c['lapsed']}, never {all_c['never']}")

    cutoff_label = fmt_date(cutoff)
    out = []

    out.append(f'''
  <div class="hdr">
    <h1>CatNav Adoption — Commercial Team</h1>
    <p>Roster: Commercial Team directory ({fmt_date(today_sydney().isoformat())}) &nbsp;·&nbsp; Usage: <code>prod.usage_by_user</code> through week ending {fmt_date(max_week)}</p>
  </div>

  <!-- KPI strip: 5 tiles -->
  <div class="kpi-strip">
    <div class="kpi hi">
      <div class="kpi-label">Ever adopted</div>
      <div class="kpi-val">{pct(all_c['ever'], all_c['n'])}</div>
      <div class="kpi-sub">{all_c['ever']} of {all_c['n']} people</div>
    </div>
    <div class="kpi hi">
      <div class="kpi-label">Active — last 90 days</div>
      <div class="kpi-val">{pct(all_c['fresh'], all_c['n'])}</div>
      <div class="kpi-sub">{all_c['fresh']} of {all_c['n']} since {cutoff_label[:-5]}</div>
    </div>
    <div class="kpi lap">
      <div class="kpi-label">Lapsed</div>
      <div class="kpi-val">{pct(all_c['lapsed'], all_c['n'])}</div>
      <div class="kpi-sub">{all_c['lapsed']} adopted, not recent</div>
    </div>
    <div class="kpi nvr">
      <div class="kpi-label">Never used</div>
      <div class="kpi-val">{pct(all_c['never'], all_c['n'])}</div>
      <div class="kpi-sub">{all_c['never']} of {all_c['n']} people</div>
    </div>
    <div class="kpi">
      <div class="kpi-label">Roster</div>
      <div class="kpi-val">{all_c['n']}</div>
      <div class="kpi-sub">{cm_c['n']} CMs · {acm_c['n']} ACMs</div>
    </div>
  </div>

  <!-- Stacked bar -->
  <div class="adopt-bar-wrap">
    <div class="adopt-bar">
      <div class="adopt-bar-seg fresh"  style="flex:{all_c['fresh']}" title="Active last 90 days: {all_c['fresh']}">{pct(all_c['fresh'], all_c['n'])}</div>
      <div class="adopt-bar-seg lapsed" style="flex:{all_c['lapsed']}"  title="Lapsed: {all_c['lapsed']}">{short_pct(all_c['lapsed'], all_c['n'])}</div>
      <div class="adopt-bar-seg never"  style="flex:{all_c['never']}"   title="Never adopted: {all_c['never']}">{pct(all_c['never'], all_c['n'], 0)}</div>
    </div>
    <div class="bar-legend">
      <div class="bar-legend-item"><span class="bar-swatch fresh"></span><strong>{all_c['fresh']}</strong>&nbsp;Active (last 90 days)</div>
      <div class="bar-legend-item"><span class="bar-swatch lapsed"></span><strong>{all_c['lapsed']}</strong>&nbsp;Lapsed (adopted but not recent)</div>
      <div class="bar-legend-item"><span class="bar-swatch never"></span><strong>{all_c['never']}</strong>&nbsp;Never adopted</div>
    </div>
  </div>
''')

    def role_card(cls, label, c):
        return f'''      <div class="role-card {cls}">
        <div class="role-head">
          <span class="role-name">{label} ({c['n']})</span>
          <span class="role-pct">{pct(c['ever'], c['n'])} ever</span>
        </div>
        <div class="role-mini-bar">
          <span style="flex:{c['fresh']};background:var(--accent)"></span>
          <span style="flex:{c['lapsed']};background:var(--lapsed)"></span>
          <span style="flex:{c['never']};background:var(--warn)"></span>
        </div>
        <div class="role-rows">
          <div class="role-row"><span class="role-row-label">Active last 90 days</span><span class="role-row-val fresh">{c['fresh']} / {c['n']} = {pct(c['fresh'], c['n'])}</span></div>
          <div class="role-row"><span class="role-row-label">Lapsed</span><span class="role-row-val lapsed">{c['lapsed']} / {c['n']} = {pct(c['lapsed'], c['n'])}</span></div>
          <div class="role-row"><span class="role-row-label">Never used</span><span class="role-row-val never">{c['never']} / {c['n']} = {pct(c['never'], c['n'])}</span></div>
        </div>
      </div>'''

    out.append(f'''  <!-- Role breakdown -->
  <div class="section">
    <div class="section-title">By role</div>
    <div class="role-grid">
{role_card("cm", "Category Managers", cm_c)}
{role_card("acm", "Asst Category Managers", acm_c)}
    </div>
  </div>
''')

    def split_card(cls, eyebrow, subtitle, group, active_colour, active_cls, never_cls):
        c = counts(group)
        sub_rows = []
        for role in ("CM", "ACM"):
            rc = counts([p for p in group if has_role(p, role)])
            sub_rows.append(f'''          <div class="split-sub-row">
            {badge(role)}
            <span class="split-sub-count">{rc['n']} people</span>
            <span class="split-sub-bar"><span style="flex:{rc['fresh']};background:{active_colour}"></span><span style="flex:{rc['lapsed']};background:var(--lapsed)"></span><span style="flex:{rc['never']};background:var(--warn)"></span></span>
            <span class="split-sub-stats">{pct(rc['fresh'], rc['n'])} active <span class="dim">· {short_pct(rc['ever'], rc['n'])} ever</span></span>
          </div>''')
        sub = "\n".join(sub_rows)
        return f'''      <div class="split-card {cls}">
        <div class="split-eyebrow">{eyebrow}</div>
        <div class="split-subtitle">{subtitle} &nbsp;({c['n']} CMs &amp; ACMs)</div>
        <div class="split-big">{short_pct(c['ever'], c['n'])}</div>
        <div class="split-bigsub">ever adopted &nbsp;·&nbsp; {c['ever']} of {c['n']}</div>
        <div class="split-bar">
          <span style="flex:{c['fresh']};background:{active_colour}"></span>
          <span style="flex:{c['lapsed']};background:var(--lapsed)"></span>
          <span style="flex:{c['never']};background:var(--warn)"></span>
        </div>
        <div class="split-rows">
          <div class="split-row"><span class="split-row-label">Active last 90 days</span><span class="split-row-val {active_cls}">{c['fresh']} / {c['n']} = {pct(c['fresh'], c['n'])}</span></div>
          <div class="split-row"><span class="split-row-label">Lapsed</span><span class="split-row-val lap">{c['lapsed']} / {c['n']} = {pct(c['lapsed'], c['n'])}</span></div>
          <div class="split-row"><span class="split-row-label">Never used</span><span class="split-row-val {never_cls}">{c['never']} / {c['n']} = {short_pct(c['never'], c['n'])}</span></div>
        </div>
        <div class="split-sub">
          <div class="split-sub-hdr">By role</div>
{sub}
        </div>
      </div>'''

    out.append(f'''  <!-- Fresh vs ambient category split -->
  <div class="section">
    <div class="section-title">Category split — fresh food vs all other categories</div>
    <div class="split-grid">
{split_card("s-fresh", "Fresh categories", "Fruit &amp; Veg · Meat · Poultry · Seafood", fresh_people, "var(--accent)", "act", "none")}
{split_card("s-ambient", "All other categories", "Ambient, chilled, deli, frozen, non-food &amp; all remaining", other_people, "var(--cm-hue)", "act", "nvr")}
    </div>
  </div>
''')

    lapsed = sorted((p for p in people if p["status"] == "lapsed"), key=lambda p: p["last"], reverse=True)
    lapsed_rows = "\n".join(
        f'        <div class="info-row">{badge(p["role"])}<span class="info-name">{esc(p["name"])}</span>'
        f'<span class="info-meta">Last: {fmt_date(p["last"])}{" · " + esc(LEAVE_NOTES[p["name"]]) if p["name"] in LEAVE_NOTES else ""}</span></div>'
        for p in lapsed)
    never = sorted((p for p in people if p["status"] == "never"), key=lambda p: (p["role"] != "ACM", p["name"]))
    never_rows = "\n".join(
        f'        <div class="info-row">{badge(p["role"])}<span class="info-name">{esc(p["name"])}</span>'
        + (f'<span class="info-meta">{esc(LEAVE_NOTES[p["name"]])}</span>' if p["name"] in LEAVE_NOTES else "") + '</div>'
        for p in never)

    out.append(f'''  <!-- Lapsed -->
  <div class="section">
    <div class="section-title">Lapsed — adopted but not active since {cutoff_label} ({all_c['lapsed']})</div>
    <div class="info-box lapsed-box">
      <div class="info-box-title">
        <svg width="13" height="13" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="8" cy="8" r="7"/><path d="M8 8V5M8 11h.01"/></svg>
        These people have used CatNav previously but haven't logged in during the last 90 days.
      </div>
      <div class="info-grid">
{lapsed_rows}
      </div>
    </div>
  </div>

  <!-- Never adopted -->
  <div class="section">
    <div class="section-title">Never adopted ({all_c['never']})</div>
    <div class="info-box never-box">
      <div class="info-box-title">
        <svg width="13" height="13" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="8" cy="8" r="7"/><path d="M8 5v3M8 11h.01"/></svg>
        Zero usage recorded since CatNav launch (May 2024).
      </div>
      <div class="info-grid">
{never_rows}
      </div>
    </div>
  </div>

  <!-- Full roster table -->
  <div class="section">
    <div class="section-title">Full roster — adopted ({all_c['ever']})</div>
    <div class="tbl-wrap">
      <div class="tbl-controls">
        <label for="role-filter">Role</label>
        <select id="role-filter" onchange="renderTable()">
          <option value="">All</option>
          <option value="CM">CM</option>
          <option value="ACM">ACM</option>
        </select>
        <label for="fresh-filter">Status</label>
        <select id="fresh-filter" onchange="renderTable()">
          <option value="">All</option>
          <option value="fresh">Active (last 90d)</option>
          <option value="lapsed">Lapsed</option>
        </select>
        <input id="search-box" type="search" placeholder="Search name…" oninput="renderTable()" style="width:130px">
        <span class="tbl-count" id="tbl-count"></span>
      </div>
      <div class="tbl-overflow">
        <table>
          <thead>
            <tr>
              <th onclick="sortBy('name')" id="th-name">Name <span class="sa">↕</span></th>
              <th onclick="sortBy('role')" id="th-role">Role <span class="sa">↕</span></th>
              <th onclick="sortBy('status')" id="th-status">Status <span class="sa">↕</span></th>
              <th onclick="sortBy('weeks')" id="th-weeks" class="sorted">Weeks <span class="sa">↓</span></th>
              <th onclick="sortBy('days')" id="th-days">Days <span class="sa">↕</span></th>
              <th onclick="sortBy('last')" id="th-last">Last seen <span class="sa">↕</span></th>
            </tr>
          </thead>
          <tbody id="roster-body"></tbody>
        </table>
      </div>
    </div>
  </div>

  <footer>
    90-day recency cutoff: {cutoff_label} &nbsp;·&nbsp; Roster: {cm_c['n']} CMs, {acm_c['n']} ACMs, {n_both} hold both roles = {all_c['n']} unique individuals &nbsp;·&nbsp; Fresh = Seafood, Meat (incl. Poultry) and Fruit &amp; Veg sections of the directory &nbsp;·&nbsp; Data: <code>gcp-wow-wiq-kscope-prod.prod.usage_by_user</code>
  </footer>
''')

    adopted = sorted((p for p in people if p["status"] != "never"), key=lambda p: -p["weeks"])
    roster_js = "[\n" + ",\n".join(
        "  " + json.dumps({"name": p["name"], "role": p["role"], "weeks": p["weeks"],
                           "days": p["days"], "last": p["last"]}, ensure_ascii=False)
        for p in adopted) + "\n]"
    # Names go into a JS literal rendered via innerHTML; escape '<' so no markup can break out.
    roster_js = roster_js.replace("<", "\\u003c")

    with open(TEMPLATE_PATH) as f:
        template = f.read()
    page = (template.replace("__BODY__", "".join(out))
                    .replace("__CUTOFF__", cutoff)
                    .replace("__ROSTER__", roster_js))
    assert "__BODY__" not in page and "__ROSTER__" not in page and "__CUTOFF__" not in page
    with open(OUTPUT_PATH, "w") as f:
        f.write(page)
    print(f"Wrote {OUTPUT_PATH}")
    return people, fresh_people


if __name__ == "__main__":
    if "--no-pull" not in sys.argv:
        pull_sheet()
    build()
