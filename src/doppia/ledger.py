"""Ledger: the in-memory transaction list backed by a CountAbout-format CSV file.

The CSV is the single source of truth. Each Transaction keeps its 11 fields as
the exact strings found in the file, so unchanged rows are written back
byte-for-byte. Quoting follows CountAbout's convention: a field is quoted only
if it contains a space, comma, double quote, or newline.
"""

import csv
import datetime as dt
import functools
import os
import shutil
import uuid
from collections import defaultdict
from dataclasses import dataclass, field, fields
from decimal import Decimal

COLUMNS = [
    "Account",
    "Date",
    "Description",
    "Category",
    "Split",
    "Split ID",
    "Amount",
    "Ref",
    "Memo",
    "Tags",
    "Cleared",
]
DATE_FMT = "%m-%d-%Y"
SPLIT_CATEGORY = "{SPLIT}"
NO_SPLIT_ID = "NA"
# Fields that every line of a split must share, beyond those edited through the dialog.
SPLIT_SHARED_ATTRS = ("description", "ref", "tags")
OPENING_DESCRIPTION = "Beginning Balance"
# Text fields open to search and replace: display name -> Transaction attribute.
SEARCH_FIELDS = {"Description": "description", "Category": "category", "Memo": "memo", "Ref": "ref", "Tags": "tags"}


@functools.lru_cache(maxsize=None)
def parse_date(text):
    """Parse an MM-DD-YYYY date. Memoized: strptime is slow and dates repeat heavily."""
    return dt.datetime.strptime(text, DATE_FMT).date()


def _quote(text):
    if text and any(c in text for c in ' ,"\n'):
        return '"' + text.replace('"', '""') + '"'
    return text


def new_split_id():
    return uuid.uuid4().hex[:12]


def format_amount(value):
    return f"{Decimal(value).quantize(Decimal('0.01')):.2f}"


def transfer_category(account):
    return "{" + account + "}"


def transfer_target(category):
    """Return the account named by a transfer category like '{Checking}', else None."""
    if category.startswith("{") and category.endswith("}") and category != SPLIT_CATEGORY:
        return category[1:-1]
    return None


@dataclass(eq=False)
class Transaction:
    """One CSV row. All attributes are the raw CSV strings.

    A split is stored as one row per line, each with its own category, amount, and memo, sharing
    a generated Split ID. There is no parent row, so sums of rows stay correct everywhere.
    CountAbout's own {SPLIT} rows have Split ID NA and no lines (is_old_split)."""

    lines = ()  # the rows a SplitSummary stands for; not a CSV field

    account: str = ""
    date_str: str = ""
    description: str = ""
    category: str = ""
    split: str = "N"
    split_id: str = "NA"
    amount_str: str = "0.00"
    ref: str = ""
    memo: str = ""
    tags: str = ""
    cleared: str = ""

    @classmethod
    def from_row(cls, row):
        return cls(*row)

    def to_row(self):
        return [getattr(self, f.name) for f in fields(self)]

    def copy(self):
        return Transaction(*self.to_row())

    @property
    def date(self):
        return parse_date(self.date_str)

    @date.setter
    def date(self, value):
        self.date_str = value.strftime(DATE_FMT)

    @property
    def amount(self):
        return Decimal(self.amount_str)

    @amount.setter
    def amount(self, value):
        self.amount_str = format_amount(value)

    @property
    def is_cleared(self):
        return self.cleared == "X"

    @is_cleared.setter
    def is_cleared(self, value):
        self.cleared = "X" if value else ""

    @property
    def is_split(self):
        return self.split == "Y"

    @property
    def split_group(self):
        """The Split ID linking this row to the other lines of its split, or None."""
        return self.split_id if self.is_split and self.split_id != NO_SPLIT_ID else None

    @property
    def is_old_split(self):
        """A CountAbout {SPLIT} row, which holds a total but no line detail."""
        return self.category == SPLIT_CATEGORY and not self.lines

    @property
    def transfer_account(self):
        return transfer_target(self.category)


class SplitSummary(Transaction):
    """One split as a single transaction: the total in CountAbout's {SPLIT} row form.
    Built on demand by Ledger.entries() and never stored in the ledger."""

    def __init__(self, lines):
        super().__init__(*lines[0].to_row())
        self.lines = lines
        self.category, self.split_id = SPLIT_CATEGORY, NO_SPLIT_ID
        self.amount = sum((t.amount for t in lines), Decimal("0"))
        self.memo = ""  # memos belong to the lines


@dataclass
class Ledger:
    transactions: list = field(default_factory=list)
    path: str = None
    dirty: bool = False

    # ---------------------------------------------------------------- file I/O

    @classmethod
    def load(cls, path):
        with open(path, newline="") as f:
            rows = list(csv.reader(f))
        if not rows or rows[0] != COLUMNS:
            raise ValueError(f"{path}: header is not CountAbout format: {rows[:1]}")
        txns = []
        for lineno, row in enumerate(rows[1:], start=2):
            if len(row) != len(COLUMNS):
                # Warn and keep going so the user sees every problem at once.
                print(f"warning: {path}:{lineno}: expected {len(COLUMNS)} fields, got {len(row)}; skipped")
                continue
            txns.append(Transaction.from_row(row))
        return cls(txns, path)

    def to_text(self, rows=None):
        lines = [",".join(_quote(c) for c in COLUMNS)]
        lines += [",".join(_quote(v) for v in t.to_row()) for t in (self.transactions if rows is None else rows)]
        return "\n".join(lines) + "\n"

    def to_countabout_text(self):
        """The ledger as CountAbout writes it: each split is one {SPLIT} row with its total,
        and the row's memo is the only place left for the memos of its lines."""
        rows = self.entries()
        for t in rows:
            if t.lines:
                t.memo = "; ".join(line.memo for line in t.lines if line.memo)
        return self.to_text(rows)

    def save(self, path=None):
        """Write atomically, keeping the previous file as <path>.bak."""
        path = path or self.path
        tmp = path + ".tmp"
        with open(tmp, "w", newline="") as f:
            f.write(self.to_text())
        if os.path.exists(path):
            shutil.copy2(path, path + ".bak")
        os.replace(tmp, path)
        self.path = path
        self.dirty = False

    # ----------------------------------------------------------------- queries

    def accounts(self):
        return sorted({t.account for t in self.transactions}, key=str.lower)

    def categories(self):
        """Non-transfer, non-split categories in use."""
        return sorted(
            {t.category for t in self.transactions if t.category and not t.category.startswith("{")},
            key=str.lower,
        )

    def descriptions(self):
        return sorted({t.description for t in self.transactions if t.description}, key=str.lower)

    def for_account(self, account):
        return [t for t in self.transactions if t.account == account]

    def entries(self, rows=None):
        """One item per real transaction, in row order: each split is collapsed to a SplitSummary
        at the position of its first line. `rows` limits this to those rows."""
        rows = self.transactions if rows is None else rows
        groups = defaultdict(list)
        for t in rows:
            if t.split_group is not None:
                groups[t.split_group].append(t)
        out = []
        for t in rows:
            if t.split_group is None:
                out.append(t)
            elif groups[t.split_group][0] is t:
                out.append(SplitSummary(groups[t.split_group]))
        return out

    def split_lines(self, txn):
        """The ledger rows of a transaction: all lines of a split, else just the row itself."""
        if txn.lines:
            return list(txn.lines)
        if txn.split_group is None:
            return [txn]
        return [t for t in self.transactions if t.split_group == txn.split_group]

    def with_split_lines(self, txns):
        """The ledger rows of these transactions, each row once."""
        rows = {id(line): line for t in txns for line in self.split_lines(t)}
        return list(rows.values())

    def account_balances(self, as_of=None):
        """{account: (balance, cleared balance)} in one pass over the ledger.
        `as_of` limits the balance (not the cleared balance) to rows on or before that date."""
        out = {}
        zero = Decimal("0")
        for t in self.transactions:
            bal, clr = out.get(t.account, (zero, zero))
            amt = t.amount
            if as_of is None or t.date <= as_of:
                bal += amt
            if t.is_cleared:
                clr += amt
            out[t.account] = (bal, clr)
        return out

    def balance(self, account, as_of=None, cleared_only=False):
        total = Decimal("0")
        for t in self.transactions:
            if t.account != account:
                continue
            if cleared_only and not t.is_cleared:
                continue
            if as_of is not None and t.date > as_of:
                continue
            total += t.amount
        return total

    def last_category_for(self, description):
        """Most recent category used with this description (for auto-fill)."""
        best = None
        key = description.strip().lower()
        for t in self.transactions:
            if t.description.strip().lower() == key and t.category and not t.is_split:
                if best is None or t.date > best.date:
                    best = t
        return best.category if best else ""

    def find_transfer_partner(self, txn):
        """Locate the mirror row of a transfer: other account, same date, negated amount."""
        other = txn.transfer_account
        if other is None:
            return None
        for t in self.transactions:
            if (
                t is not txn
                and t.account == other
                and t.transfer_account == txn.account
                and t.date_str == txn.date_str
                and t.amount == -txn.amount
            ):
                return t
        return None

    def search(self, pattern, attrs, replacement=None, accounts=None):
        """Matches of a compiled regex in the given text attributes, as (txn, attr, old, new)
        tuples in ledger order. `new` is the re.sub result, or None when only searching.
        `accounts` limits the search to those accounts (None for all)."""
        out = []
        for t in self.transactions:
            if accounts is not None and t.account not in accounts:
                continue
            for attr in attrs:
                old = getattr(t, attr)
                if pattern.search(old):
                    new = None if replacement is None else pattern.sub(replacement, old)
                    out.append((t, attr, old, new))
        return out

    @staticmethod
    def replacement_problem(attr, old, new):
        """Why a replacement can't be applied, or None if it can."""
        if new == old:
            return "no change"
        if "\n" in new or "\r" in new:
            return "would contain a line break"
        if attr == "category" and (old.startswith("{") or new.startswith("{")):
            # Transfer categories name accounts and have mirror rows; {SPLIT} marks splits.
            return "transfer/split category (use Account → Rename account)"
        return None

    def apply_replacements(self, changes):
        """Apply (txn, attr, new) changes. Callers check replacement_problem() first."""
        for t, attr, new in changes:
            for row in self.split_lines(t) if attr in SPLIT_SHARED_ATTRS else [t]:
                setattr(row, attr, new)
        if changes:
            self.dirty = True

    # ---------------------------------------------------------------- mutation

    def _insert_sorted(self, *txns):
        """Insert adjacent rows of one date, keeping the file's newest-first date order;
        new rows go first within a date."""
        d = txns[0].date
        for i, t in enumerate(self.transactions):
            if t.date <= d:
                self.transactions[i:i] = txns
                return
        self.transactions.extend(txns)

    def _insert_mirror(self, txn):
        """Give a new transfer its mirror row in the other account."""
        other = txn.transfer_account
        if other is not None and other != txn.account:
            mirror = txn.copy()
            mirror.account = other
            mirror.category = transfer_category(txn.account)
            mirror.amount = -txn.amount
            mirror.cleared = ""
            self._insert_sorted(mirror)

    def add(self, txn):
        """Add a transaction. A transfer also gets its mirror row in the other account."""
        self._insert_sorted(txn)
        self._insert_mirror(txn)
        self.dirty = True

    def add_split(self, lines):
        """Add the lines of a new split, which already share a Split ID."""
        self._insert_sorted(*lines)
        self.dirty = True

    def update_split(self, txn, new_lines):
        """Replace a transaction with these rows, keeping its place within its date. Handles a
        split being edited, a single row becoming a split, and a split going back to one row."""
        old = self.split_lines(txn)
        partner = self.find_transfer_partner(old[0])
        doomed = {id(t) for t in old[1:]} | {id(partner)}
        self.transactions = [t for t in self.transactions if id(t) not in doomed]
        i = self.transactions.index(old[0])
        if old[0].date_str == new_lines[0].date_str:
            self.transactions[i : i + 1] = new_lines
        else:
            del self.transactions[i]
            self._insert_sorted(*new_lines)
        if len(new_lines) == 1:
            self._insert_mirror(new_lines[0])
        self.dirty = True

    def update(self, txn, new):
        """Replace txn's contents with those of `new`, keeping transfer mirrors in sync."""
        partner = self.find_transfer_partner(txn)
        if partner is not None:
            self.transactions.remove(partner)
        date_changed = txn.date_str != new.date_str
        for f in fields(Transaction):
            setattr(txn, f.name, getattr(new, f.name))
        if date_changed:
            self.transactions.remove(txn)
            self._insert_sorted(txn)
        other = txn.transfer_account
        if other is not None and other != txn.account:
            mirror = txn.copy()
            mirror.account = other
            mirror.category = transfer_category(txn.account)
            mirror.amount = -txn.amount
            mirror.cleared = partner.cleared if partner is not None else ""
            if partner is not None:
                mirror.memo = partner.memo
                mirror.ref = partner.ref
            self._insert_sorted(mirror)
        self.dirty = True

    def delete(self, txn, with_partner=True):
        partner = self.find_transfer_partner(txn) if with_partner else None
        for row in self.split_lines(txn):
            self.transactions.remove(row)
        if partner is not None:
            self.transactions.remove(partner)
        self.dirty = True

    def with_transfer_partners(self, txns):
        """The given rows plus each transfer's mirror row. Each mirror pairs with one transfer,
        preferring a mirror that is itself in `txns`, so identical transfers keep their own pairs."""
        by_key = defaultdict(list)
        for t in self.transactions:
            if t.transfer_account is not None:
                by_key[t.account, t.transfer_account, t.date_str, t.amount].append(t)
        chosen = {id(t) for t in txns}
        paired = set()
        out = list(txns)
        for t in txns:
            if id(t) in paired or t.transfer_account is None:
                continue
            paired.add(id(t))
            rows = by_key[t.transfer_account, t.account, t.date_str, -t.amount]
            free = [r for r in rows if id(r) not in paired]
            partner = next((r for r in free if id(r) in chosen), free[0] if free else None)
            if partner is not None:
                paired.add(id(partner))
                if id(partner) not in chosen:
                    out.append(partner)
        return out

    def delete_rows(self, txns):
        """Remove exactly these rows (see with_transfer_partners for including mirrors)."""
        doomed = {id(t) for t in txns}
        self.transactions = [t for t in self.transactions if id(t) not in doomed]
        self.dirty = True

    def set_cleared(self, txn, cleared):
        txn.is_cleared = cleared
        for row in self.split_lines(txn):
            row.is_cleared = cleared
        self.dirty = True

    def create_account(self, name, opening_balance=Decimal("0"), date=None):
        """Accounts only exist through their rows, so a new account gets an opening-balance row
        (the same convention CountAbout uses for 'Beginning Balance')."""
        if name in self.accounts():
            raise ValueError(f"Account {name!r} already exists")
        txn = Transaction(account=name, description=OPENING_DESCRIPTION, cleared="X")
        txn.date = date or dt.date.today()
        txn.amount = opening_balance
        self.add(txn)
        return txn

    def rename_account(self, old, new):
        if new in self.accounts():
            raise ValueError(f"Account {new!r} already exists")
        for t in self.transactions:
            if t.account == old:
                t.account = new
            if t.category == transfer_category(old):
                t.category = transfer_category(new)
        self.dirty = True

    def rename_category(self, old, new):
        for t in self.transactions:
            if t.category == old:
                t.category = new
        self.dirty = True
