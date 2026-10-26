# vibeacct

Personal accounting for bank and credit card accounts (PySide6 desktop GUI).
Data lives in a single CSV in CountAbout export format; files are written back
byte-for-byte compatible (unchanged rows are untouched, quoting matches CountAbout).

## Run

    uv venv .venv && uv pip install --python .venv/bin/python -e '.[test]'
    .venv/bin/vibeacct path/to/countabout-export.csv     # reopens last file if omitted
    .venv/bin/python -m pytest

## Features

- Account list with today's balance, cleared balance, net worth. Future-dated rows shown grey.
- Register per account or all accounts: running balance, text filter, uncleared-only filter.
- New/edit/delete (Ctrl+N / Ctrl+E / Ctrl+Del), Ctrl+K or double-click Clr to toggle cleared.
  Payee memory: typing a known description fills its last category and amount.
- Transfers use CountAbout's `{Account}` category; the mirror row in the other account
  is created/updated/deleted automatically (matched by date and negated amount).
- Bank CSV import (Ctrl+I): column mapping saved as named profiles in
  `~/.config/vibeacct/import_profiles.json`; payee/category matched from history;
  duplicates (same amount within 3 days) unchecked by default; imported rows marked cleared.
- Reconcile (Ctrl+R) against a statement ending balance.
- Reports (Ctrl+P): totals by category, category by month, income vs. expense by month.
- Save (Ctrl+S) writes atomically and keeps the previous file as `<file>.bak`.

## Limitations (from the CountAbout format)

- Split transactions: existing `{SPLIT}` rows are preserved, but the export carries no
  split detail, so new splits cannot be created and reports show splits as one lump.
- Balances are sums of rows; there is no separate opening-balance field. New accounts
  get a "Beginning Balance" row.
- `Cleared` is X/blank only (no separate reconciled state).
