# doppia

Doppia is a personal accounting program.
I used to use Quicken, but then I moved to Linux and had to run it in a Windows VM.
After a few years of that and all the nagging to update, I moved to [Countabout](https://countabout.com/).
Its web interface let me use it anywhere, but it was tedious to login and enter transactions.
And it cost an extra $35/year if I wanted to sync transactions from my bank/credit accounts.

So I built Doppia. With Claude. In a couple of hours.

Doppia has the following advantages:

* It's multi-OS (it's a Python app so it runs on Windows or Linux and -maybe- MacOS).
* It's local (data is kept in readable CSV files).
* It's less effort (copy-paste multiple transactions directly from account webpages).
* It's fast (well, faster than going through a web interface).
* It's free (MIT license, no subscriptions, no nothing).
* It's adaptable (just ask Claude and get new features).

That's all for me. Now on to the AI-speak...

## The name

doppia is named after *partita doppia*, Italian for double-entry bookkeeping. The method was
first described in print by Luca Pacioli (c. 1447–1517), an Italian mathematician and
Franciscan friar, in his *Summa de arithmetica, geometria, proportioni et proportionalita*
(Venice, 1494), which earned him the title "father of accounting".

## Install

Install doppia from PyPI as a standalone app with
[pipx](https://pipx.pypa.io) or [uv](https://docs.astral.sh/uv/), which keep it isolated
without you having to manage a virtual environment:

    pipx install doppia          # or: uv tool install doppia

Python 3.10 or later is required. The clipboard import and **Guess with LLM** features also
need [Ollama](https://ollama.com) with a model installed, e.g. `ollama pull gemma4:12b-it-qat`;
everything else works without it.

## Run

    doppia path/to/countabout-export.csv     # starts a new, empty ledger if omitted

## Development

From a clone of the repository, [uv](https://docs.astral.sh/uv/) runs the app and tests
with the dependencies it needs:

    uv run doppia path/to/countabout-export.csv
    uv run --extra test pytest

## What's new in 0.4.0

- **File → New** (Ctrl+Shift+N) starts an empty ledger, so you can build one from scratch instead
  of starting from a CountAbout export. Add accounts with **Account → New account**, then
  transactions. Save asks for a file name the first time.
- Running `doppia` without a file now starts a new ledger and no longer reopens the last file.
- Running `doppia` with a file that doesn't exist now shows an error, then opens an empty ledger.

## What's new in 0.3.0

- **Split transactions.** The green **+** next to Category in the transaction dialog divides a
  transaction into lines, each with its own category, payment or deposit, and memo. The first
  line absorbs any change to the total or the other lines. A warning appears if it flips between
  payment and deposit. Transfers can't be split.
  - The register shows the `{SPLIT}` total with the lines indented below, and a split stays
    together under every sort. Highlighting, clearing, deleting, or editing any of its rows acts
    on the whole split.
  - Reports count each line under its own category. Reconcile and bank-import duplicate detection
    use the total.
  - Lines can be added to an existing CountAbout `{SPLIT}` row.
- **File → Export for CountAbout…** writes a separate CSV in CountAbout's format, with each split
  as one `{SPLIT}` total row. Split lines are stored as rows with a shared `Split ID`, which
  CountAbout's own export never writes, so use this to transfer a ledger. It hasn't been tested
  with a CountAbout import.
- Drag a column header to reorder the register. The order is remembered between runs, and
  **View → Reset column order** restores the default.
- The Memo column is now last by default, after Clr.

## What's new in 0.2.0

- Right-click a transaction to **Edit** or **Copy** it. A copy is dated today and not cleared.
- Green **+** and red **-** buttons replace the New, Edit, and Delete buttons.
- Delete and Toggle cleared now act on every highlighted transaction.
- **View → Hide cleared** replaces the Uncleared only checkbox, and **View → Hide future** is new.
  A note beside the account name shows how many transactions are hidden, with a breakdown on hover.
- OK stays disabled on a new transaction until it has a description, a category, and an amount.
- Columns are sized to their contents on the first run, and **View → Resize columns to fit** refits
  them on demand. The Clr column moved to the far right and the dates are centered.
- The window size and position and the column widths are remembered between runs.

## Features

- Account list with today's balance and cleared balance. The bold **Total** row sums both
  columns over the selected accounts (all accounts when none or "All accounts" is selected).
  Future-dated rows shown grey.
- Money is shown as `$1,234.56`: red for negative, green for positive, no minus sign
  (the minus is kept only in plain-text messages that can't be colored). Colors stay
  visible on selected rows. `$` and commas are accepted when typing amounts.
- Register per account or all accounts: running balance, **View → Hide cleared** and
  **View → Hide future** toggles (the Balance column still counts hidden rows; while rows are
  hidden, a note beside the account name shows how many, and hovering over it gives the
  breakdown), and a filter field that takes a case-insensitive regular expression (e.g. `^amazon`, `3000`,
  `\$3,000`) matched in the description, category, memo, ref, date, or amount. Its X button
  clears it; an invalid expression tints the field and is ignored. The account names over
  the register are cut to 30 characters, including `...` (hover for the full list).
  - Ctrl/Shift-click in the account list to select several accounts; the register shows their
    combined transactions with a combined running balance. Reconcile and Rename account need
    a single account selected.
  - Column widths are remembered between runs. On the very first run they're sized to their
    contents and, if they don't all fit the pane, Memo, then Description, Category, and Account
    are narrowed as needed, each down to the width that cuts off fewer than 5% of its entries.
    After that they're only changed by dragging them or by **View → Resize columns to fit**.
    Date, Ref, Amount, and Balance widen when needed so they never truncate.
  - The Clr column is always just wide enough for its label.
  - **View → Resize columns to fit** changes every column except Clr by the same number of pixels
    to fill the pane. When shrinking, Date, Ref, Amount, and Balance stop at their contents and the
    others at their headers, and the remaining columns share the rest.
  - Drag a column header to move the column. The order is remembered between runs, and
    **View → Reset column order** restores the default.
  - Click a column header to sort by it; click again to reverse. The sort column's header
    is highlighted, and the selection is kept when re-sorting. The register opens sorted by
    date, newest first. The Balance column is a date-order running balance, so it only
    adds up when sorted by date.
  - Click the Clr box to toggle cleared; cleared rows show a green ✔ (the CSV still stores `X`).
  - Double-click a row to edit it. Right-click for a menu to edit it, delete it, or copy it
    (**Copy** opens a new transaction filled in from that one, dated today and not cleared;
    a copied split keeps its lines, and a copied CountAbout `{SPLIT}` row starts with a blank
    category).
- **Split transactions**: in the New/Edit Transaction dialog, the green + next to Category opens
  a pane of lines, each with its own category, payment or deposit, and memo. The first line is the
  original transaction, and a second line starts at 0.00. The + below the last line adds more, and
  the red - on a line removes it. Removing all the added lines closes the pane. Enter the bank total as usual: the first line absorbs any change
  to the total or the other lines. OK stays disabled until every line has a category and an amount
  and **Remaining** reaches $0.00 (it can differ if you edit the first line directly). A warning
  appears if the other lines exceed the total, so that the first line flips between payment and
  deposit. Transfers can't be split, so the + is disabled for them, and lines can't be transfers.
  - The register shows the `{SPLIT}` total and running balance with the lines indented below. A
    split stays together under every sort, and highlighting, clearing, deleting, or editing any
    of its rows acts on the whole split.
  - Reports count each line under its own category. Reconcile and bank-import duplicate detection
    use the split's total.
  - Each line is stored as a CSV row with a shared `Split ID`, and there is no separate total row.
  - Add lines to an existing CountAbout `{SPLIT}` row to give it detail.
- New/edit/delete (Ctrl+N / Ctrl+E / Ctrl+Del), Ctrl+K toggles cleared on each highlighted
  transaction. OK stays disabled on a new transaction until it has a description, a category,
  and an amount. The green + and red - buttons above the register add a transaction and delete
  the highlighted ones (Ctrl/Shift-click to highlight several); deleting always asks first and
  also removes the mirror row of a transfer.
  Payee memory: typing a known description fills its last category and amount.
- Search and replace (Transaction menu, Ctrl+H): find a regular expression in the
  Description, Category, Memo, Ref, and/or Tags fields (optionally case-sensitive, optionally
  only in the selected accounts). Check **Replace with** to preview each change (`\1` inserts a
  matched group), untick any you don't want (**Select All/None** flips them all), then
  **Replace checked**. Transfer/split categories and results containing line breaks can't be
  replaced. Nothing is written to the file until you save.
- Transfers use CountAbout's `{Account}` category; the mirror row in the other account
  is created/updated/deleted automatically (matched by date and negated amount).
- Bank CSV import (Ctrl+I): column mapping saved as named profiles in
  `~/.config/doppia/import_profiles.json`; payee/category matched from history;
  duplicates (same amount within 3 days) unchecked by default; imported rows marked cleared.
  - **Guess with LLM** asks the local model (the one chosen in clipboard import) which columns
    hold the date, description, amount or debit/credit, memo, and check number, and the date
    format. Only the header and first 8 rows are sent. Its answer is limited to the file's
    real column names and checked against the sample rows; **Flip signs** is set from the
    data (mostly positive amounts = card charges listed as positive), not by the model.
    Takes a few seconds; save the result as a profile to skip it next time.
- Import from clipboard (File menu, Ctrl+Shift+V): copy transactions from a bank or card
  website or statement, and a local LLM served by [Ollama](https://ollama.com) turns the text
  into transactions for the chosen account (the active one by default). Nothing leaves your
  machine. The text is shown for editing; press **Parse** (about 15–30 s on an RTX 3060).
  - Model: any installed Ollama model, remembered between runs. Default `gemma4:12b-it-qat`,
    which read sample bank and card text correctly; smaller models got dates and signs wrong.
    Set `OLLAMA_HOST` if Ollama isn't at `localhost:11434`.
  - Likely duplicates of existing transactions (same amount within 3 days) are removed from the
    list; **Show likely duplicates** brings them back, unchecked. Payees and categories are
    matched from history, as in CSV import.
  - Review before importing: amounts the model returns that don't appear in the pasted text
    are unchecked and flagged, and **Flip signs** fixes deposits/charges read backwards.
  - Imported rows are marked cleared (optional) except ones shown as pending.
- Reconcile (Ctrl+R) against a statement ending balance: check off uncleared
  transactions until the difference is $0.00.
- Reports (Ctrl+P): totals by category, category by month, income vs. expense by month.
  Click a column header to sort.
  - Transfers are excluded unless **Include transfers** is checked. Then each transfer is
    reported under `Transfer: <other account>`. A transfer between two checked accounts appears
    on both sides, so it adds to both income and expense while netting to zero.
  - **Accounts All/None** checks or unchecks every account in the account list.
  - **Categories** field: a case-insensitive regular expression matched anywhere in the
    category name (e.g. `^(groceries|dining)$`). Press Enter or Run to apply; the X button
    clears it and reruns. An invalid expression is reported and ignored.
  - Ctrl/Shift-click category rows, then Run, to report only those categories. The
    restriction persists across runs until **Show all categories** clears it (along with
    the regex). Run uses whatever rows are highlighted when pressed.
  - Total income, expense, and net rows reflect only the categories shown.
  - **Save…** writes the report as shown (current sort order) to CSV (plain signed numbers for
    spreadsheets), Markdown, plain text (aligned columns), or PDF. Markdown, text, and PDF
    include the report settings (dates, accounts, category filters) and show amounts with a
    minus sign, since they may be printed without color. Wide month-by-category reports print landscape in a smaller font.
- View → Font… sets the font family, style (regular/italic/bold), and size for the whole
  app; the choice is remembered. View → Reset font returns to the system default. After a
  font change, View → Resize columns to fit refits the columns.
- The window's size and position are remembered between runs.
- **File → New** (Ctrl+Shift+N) starts an empty ledger: add accounts with **Account → New
  account**, then transactions. Save asks for a file name the first time.
- Save (Ctrl+S) writes atomically and keeps the previous file as `<file>.bak`.
- Handles a 10k+ row ledger quickly: about 0.6 s to start and 0.15 s to switch accounts.

## Limitations (from the CountAbout format)

- Split transactions: CountAbout's export carries no split detail, so its `{SPLIT}` rows show as
  one lump in reports until you add lines to them. Rows with a real `Split ID` are doppia's own
  extension of the format, so use **File → Export for CountAbout…** to transfer a ledger: it
  writes a separate CSV with each split as one `{SPLIT}` total row, without the line detail (line
  memos are joined into that row's memo). The export hasn't been tested with a CountAbout import.
- Balances are sums of rows; there is no separate opening-balance field. New accounts
  get a "Beginning Balance" row.
- `Cleared` is X/blank only (no separate reconciled state).

## License

MIT; see [LICENSE](https://github.com/devbisme/doppia/blob/master/LICENSE).
