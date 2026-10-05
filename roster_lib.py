"""Parse the Commercial Team directory (Google Sheet) into a CM/ACM roster.

Mirrors the logic used for the original 18 Sep 2026 CatNav Adoption page.
"""
import json


def load_sheet_rows(path):
    """Read a saved `gws sheets ... values get` response, skipping the keyring banner line."""
    with open(path) as f:
        text = f.read()
    return json.loads(text[text.index('{'):])['values']


def _cell(row, idx):
    return row[idx].strip() if len(row) > idx else ''


def parse_roster(rows):
    """Return {key: {name, email, role, category, note}} for every named CM/ACM.

    key is the lower-cased email (falls back to lower-cased name).
    Someone listed as both CM and ACM gets role 'CM+ACM'.
    """
    roster = {}
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
        role = 'CM' if is_cm else 'ACM'
        key = email if '@' in email else name.lower()
        entry = {'name': name, 'email': email, 'role': role,
                 'category': _cell(row, 0), 'note': _cell(row, 5)}
        if key in roster and roster[key]['role'] != role:
            roster[key]['role'] = 'CM+ACM'
        elif key not in roster:
            roster[key] = entry
    return roster
