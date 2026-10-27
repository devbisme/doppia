"""Import bank/credit-card CSV downloads into a Ledger.

A Profile maps a bank's CSV columns onto transaction fields. Profiles are kept
in a JSON file outside the ledger CSV so the ledger stays strictly CountAbout format.
"""

import csv
import datetime as dt
import json
import os
import re
from dataclasses import asdict, dataclass, field
from decimal import Decimal, InvalidOperation

from .ledger import Transaction

PROFILE_PATH = os.path.join(
    os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config")), "pacioli", "import_profiles.json"
)
DATE_FORMATS = ["%m/%d/%Y", "%m/%d/%y", "%Y-%m-%d", "%m-%d-%Y", "%d/%m/%Y", "%b %d, %Y", "%Y/%m/%d"]
DUPLICATE_WINDOW_DAYS = 3


@dataclass
class Profile:
    name: str = ""
    date_col: str = ""
    date_format: str = ""  # blank = auto-detect
    description_col: str = ""
    amount_col: str = ""  # single signed amount column...
    debit_col: str = ""  # ...or separate debit/credit columns
    credit_col: str = ""
    memo_col: str = ""
    ref_col: str = ""
    negate: bool = False  # flip signs (e.g. card exports that show charges as positive)


def load_profiles(path=PROFILE_PATH):
    try:
        with open(path) as f:
            return {name: Profile(**p) for name, p in json.load(f).items()}
    except FileNotFoundError:
        return {}
    except (ValueError, TypeError) as e:
        print(f"warning: could not read import profiles from {path}: {e}")
        return {}


def save_profiles(profiles, path=PROFILE_PATH):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump({n: asdict(p) for n, p in profiles.items()}, f, indent=2)


def read_bank_csv(path):
    """Return (header, rows) where rows are dicts. Skips any preamble lines before the header."""
    with open(path, newline="", encoding="utf-8-sig") as f:
        lines = list(csv.reader(f))
    # The header is the first row with the modal column count (banks sometimes prepend junk).
    counts = [len(r) for r in lines if any(c.strip() for c in r)]
    width = max(set(counts), key=counts.count) if counts else 0
    start = next((i for i, r in enumerate(lines) if len(r) == width), 0)
    header = [h.strip() for h in lines[start]] if lines else []
    rows = [dict(zip(header, r)) for r in lines[start + 1 :] if len(r) == width and any(c.strip() for c in r)]
    return header, rows


def guess_profile(header):
    """Make a first-cut column mapping from header names."""

    def find(*keys):
        for h in header:
            if any(k in h.lower() for k in keys):
                return h
        return ""

    p = Profile(
        date_col=find("posted date", "post date", "posting date", "date"),
        description_col=find("description", "payee", "merchant", "name"),
        amount_col=find("amount"),
        memo_col=find("memo", "note"),
        ref_col=find("check", "ref"),
    )
    if not p.amount_col:
        p.debit_col = find("debit", "withdrawal")
        p.credit_col = find("credit", "deposit")
    return p


def parse_date(text, fmt=""):
    text = text.strip()
    for f in [fmt] if fmt else DATE_FORMATS:
        try:
            return dt.datetime.strptime(text, f).date()
        except ValueError:
            pass
    raise ValueError(f"unrecognized date {text!r}")


def parse_money(text):
    s = text.strip().replace("$", "").replace(",", "")
    if not s:
        return Decimal("0")
    neg = s.startswith("(") and s.endswith(")")
    s = s.strip("()")
    if s.endswith("-"):  # trailing minus, e.g. "12.34-"
        neg, s = True, s[:-1]
    try:
        v = Decimal(s)
    except InvalidOperation:
        raise ValueError(f"unrecognized amount {text!r}")
    return -v if neg else v


def _norm(text):
    return re.sub(r"[^a-z0-9]", "", text.lower())


@dataclass
class Candidate:
    txn: Transaction
    bank_text: str  # original description from the bank file
    duplicate_of: Transaction = None
    include: bool = True
    error: str = ""


@dataclass
class PayeeMatcher:
    """Map raw bank descriptions to payee names/categories already used in the ledger."""

    exact: dict = field(default_factory=dict)  # normalized description -> (description, category)
    by_length: list = field(default_factory=list)  # [(normalized, description, category)] longest first

    @classmethod
    def from_ledger(cls, ledger):
        latest = {}
        for t in ledger.transactions:
            if not t.description or t.is_split or t.transfer_account is not None or not t.category:
                continue
            key = _norm(t.description)
            if len(key) < 3:
                continue
            if key not in latest or t.date > latest[key].date:
                latest[key] = t
        exact = {k: (t.description, t.category) for k, t in latest.items()}
        by_length = sorted(((k, d, c) for k, (d, c) in exact.items() if len(k) >= 4), key=lambda x: -len(x[0]))
        return cls(exact, by_length)

    def match(self, bank_text):
        key = _norm(bank_text)
        if key in self.exact:
            return self.exact[key]
        for k, desc, cat in self.by_length:
            if key.startswith(k) or (len(k) >= 6 and k in key):
                return desc, cat
        return None


def build_candidates(ledger, account, rows, profile):
    """Convert bank rows to Candidates: auto-categorized, with likely duplicates flagged/excluded."""
    matcher = PayeeMatcher.from_ledger(ledger)
    existing = ledger.for_account(account)
    claimed = set()
    out = []
    for row in rows:
        bank_text = row.get(profile.description_col, "").strip()
        txn = Transaction(account=account, description=bank_text)
        cand = Candidate(txn, bank_text)
        out.append(cand)
        try:
            txn.date = parse_date(row.get(profile.date_col, ""), profile.date_format)
            if profile.amount_col:
                amount = parse_money(row.get(profile.amount_col, ""))
            else:
                amount = parse_money(row.get(profile.credit_col, "")) - abs(parse_money(row.get(profile.debit_col, "")))
            txn.amount = -amount if profile.negate else amount
        except ValueError as e:
            cand.error, cand.include = str(e), False
            continue
        if profile.memo_col:
            txn.memo = row.get(profile.memo_col, "").strip()
        if profile.ref_col:
            txn.ref = row.get(profile.ref_col, "").strip()
        m = matcher.match(bank_text)
        if m:
            txn.description, txn.category = m
        # Duplicate: same account and amount within a few days, not already matched.
        for t in existing:
            if id(t) in claimed or t.amount != txn.amount:
                continue
            if abs((t.date - txn.date).days) <= DUPLICATE_WINDOW_DAYS:
                cand.duplicate_of, cand.include = t, False
                claimed.add(id(t))
                break
    return out


def apply_candidates(ledger, candidates, mark_cleared=True):
    """Add included candidates to the ledger. Bank-downloaded rows have cleared the bank.
    A duplicate that is not imported still confirms the existing row, so it gets cleared too."""
    n = 0
    for c in candidates:
        if c.include and not c.error:
            c.txn.is_cleared = mark_cleared
            ledger.add(c.txn)
            n += 1
        elif c.duplicate_of is not None and mark_cleared and not c.duplicate_of.is_cleared:
            ledger.set_cleared(c.duplicate_of, True)
    return n
