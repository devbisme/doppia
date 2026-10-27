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
- Money is shown as `$1,234.56`: red for negative, green for positive, no minus sign
  (the minus is kept only in plain-text messages that can't be colored). Colors stay
  visible on selected rows. `$` and commas are accepted when typing amounts.
- Register per account or all accounts: running balance, text filter (matches `$3,000`
  or `3000`), uncleared-only filter.
  - Ctrl/Shift-click in the account list to select several accounts; the register shows their
    combined transactions with a combined running balance. Reconcile and Rename account need
    a single account selected.
  - Click a column header to sort by it; click again to reverse. The sort column's header
    is highlighted, and the selection is kept when re-sorting. The register opens sorted by
    date, newest first. The Balance column is a date-order running balance, so it only
    adds up when sorted by date.
  - Click the Clr box to toggle cleared; cleared rows show a green ✔ (the CSV still stores `X`).
  - Double-click a row to edit it; right-click for a menu to delete it.
- New/edit/delete (Ctrl+N / Ctrl+E / Ctrl+Del), Ctrl+K toggles cleared.
  Payee memory: typing a known description fills its last category and amount.
- Transfers use CountAbout's `{Account}` category; the mirror row in the other account
  is created/updated/deleted automatically (matched by date and negated amount).
- Bank CSV import (Ctrl+I): column mapping saved as named profiles in
  `~/.config/vibeacct/import_profiles.json`; payee/category matched from history;
  duplicates (same amount within 3 days) unchecked by default; imported rows marked cleared.
- Reconcile (Ctrl+R) against a statement ending balance: check off uncleared
  transactions until the difference is $0.00.
- Reports (Ctrl+P): totals by category, category by month, income vs. expense by month.
  Transfers are excluded. Click a column header to sort.
  - **All accounts on/off** checks or unchecks every account in the account list.
  - **Categories** field: a case-insensitive regular expression matched anywhere in the
    category name (e.g. `^(groceries|dining)$`). Press Enter or Run to apply; the X button
    clears it and reruns. An invalid expression is reported and ignored.
  - Ctrl/Shift-click category rows, then Run, to report only those categories. The
    restriction persists across runs until **Show all categories** clears it (along with
    the regex). Run uses whatever rows are highlighted when pressed.
  - Total income, expense, and net rows reflect only the categories shown.
  - **Save…** writes the report as shown (current sort order) to CSV (plain signed numbers for
    spreadsheets), Markdown, or PDF. Markdown and PDF include the report settings (dates,
    accounts, category filters) and show amounts with a minus sign, since they may be printed
    without color. Wide month-by-category reports print landscape in a smaller font.
- View → Font… sets the font family, style (regular/italic/bold), and size for the whole
  app; the choice is remembered. View → Reset font returns to the system default.
- Save (Ctrl+S) writes atomically and keeps the previous file as `<file>.bak`.
- Handles a 10k+ row ledger quickly: about 0.6 s to start and 0.1 s to switch accounts.

## Limitations (from the CountAbout format)

- Split transactions: existing `{SPLIT}` rows are preserved, but the export carries no
  split detail, so new splits cannot be created and reports show splits as one lump.
- Balances are sums of rows; there is no separate opening-balance field. New accounts
  get a "Beginning Balance" row.
- `Cleared` is X/blank only (no separate reconciled state).
