"""Parse the MASTER Commercial Contact List (Google Sheet) into the CatNav Adoption roster.

Two tabs feed the roster:
  - "Commercial Team": one row per CM / ACM (plus CAs, Buyers etc., which are excluded)
  - "Summary Tab ":    one cell per leader — "<Title>\\n\\n<Name> - <phone>\\n<email>" —
                       for Merchandise Managers, Commercial Directors and Directors
"""
import json
import re

# Display / sort order of roles; a person holding several is shown as e.g. "CM+MM".
ROLE_ORDER = ["CM", "ACM", "MM", "CD", "Director"]

EMAIL_RE = re.compile(r"[\w.\-]+@woolworths\.com\.au", re.IGNORECASE)
PHONE_RE = re.compile(r"[\s\-–]*\(?\d[\d\s()]{7,}\d.*$")

# Leadership areas counted as "fresh" for the fresh vs other split (same food scope as
# the CM/ACM split: Seafood, Meat incl. Poultry, Fruit & Veg). Word-boundary matched,
# so "Chilled Meats & Occasions" (deli) does NOT count — "Meats" isn't "Meat".
FRESH_LEADER_AREA_RE = re.compile(r"\b(Meat|Seafood|Poultry|Fruit|Veg|Vegetables|Fresh)\b", re.IGNORECASE)


def load_sheet_rows(path):
    """Read a saved Sheets values.get response, skipping any leading CLI banner line."""
    with open(path) as f:
        text = f.read()
    return json.loads(text[text.index('{'):]).get('values', [])


def join_roles(roles):
    return "+".join(r for r in ROLE_ORDER if r in roles)


def _cell(row, idx):
    return row[idx].strip() if len(row) > idx else ''


def _add(roster, key, entry, role):
    """Insert or merge a person; someone listed under two roles keeps both."""
    if key in roster:
        roster[key]['roles'].add(role)
        roster[key]['role'] = join_roles(roster[key]['roles'])
        roster[key]['is_fresh_leader'] = roster[key].get('is_fresh_leader') or entry.get('is_fresh_leader', False)
    else:
        roster[key] = {**entry, 'roles': {role}, 'role': role}


def parse_roster(rows, roster=None):
    """Add every named CM/ACM in Commercial Team tab rows to roster ({key: person}).

    key is the lower-cased email (falls back to lower-cased name).
    """
    roster = {} if roster is None else roster
    for row in rows:
        title = _cell(row, 1).upper()
        name = _cell(row, 2)
        email = _cell(row, 4).lower()
        if not name or name.upper() == 'TBC':
            continue
        is_cm = title in ('CM', 'CM: MON-WED', 'CM: WED-FRI') or title.startswith('CM ')
        is_acm = title in ('ACM', 'ACM/CA') or title.startswith('ACM ')
        if not (is_cm or is_acm):
            continue
        key = email if '@' in email else name.lower()
        _add(roster, key, {'name': name, 'email': email, 'category': _cell(row, 0), 'note': _cell(row, 5)},
             'CM' if is_cm else 'ACM')
    return roster


def leader_role(title):
    """Map a Summary Tab title to a roster role, or None for roles outside scope (Heads of, GMs)."""
    if title.startswith('Merchandise Manager'):
        return 'MM'
    if title.startswith('Commercial Director'):
        return 'CD'
    if title.startswith('Director'):
        return 'Director'
    return None


def parse_leadership(summary_rows, catnav_users=None, roster=None):
    """Add MMs, CDs and Directors from Summary Tab cells to roster.

    catnav_users: optional {lower-cased name: email} from CatNav's own AU Users sheet,
    used only to fill in leaders the directory lists without an email.
    Returns (roster, unresolved) where unresolved lists leaders with no email found.
    """
    roster = {} if roster is None else roster
    catnav_users = catnav_users or {}
    unresolved = []
    for row in summary_rows:
        for cell in row:
            lines = [ln.strip() for ln in cell.split('\n') if ln.strip()]
            if len(lines) < 2:
                continue
            title = lines[0]
            role = leader_role(title)
            if not role:
                continue
            name = PHONE_RE.sub('', lines[1]).strip(' -–')
            match = EMAIL_RE.search(cell)
            email = match.group(0).lower() if match else catnav_users.get(name.lower(), '').lower()
            if not email:
                unresolved.append(f"{name} ({title})")
                continue
            area = title.split('-', 1)[1].strip() if '-' in title else ''
            _add(roster, email, {'name': name, 'email': email, 'category': area, 'note': '',
                                 'is_fresh_leader': bool(FRESH_LEADER_AREA_RE.search(area))}, role)
    return roster, unresolved
