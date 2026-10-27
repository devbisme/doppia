"""Dialogs: transaction editor, bank CSV import, reconciliation, reports."""

import datetime as dt
import os
import re
from collections import defaultdict
from decimal import Decimal, InvalidOperation

from PySide6.QtCore import QDate, QMarginsF, Qt
from PySide6.QtGui import QDoubleValidator, QPageLayout, QPageSize, QPdfWriter, QTextDocument
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QCompleter,
    QDateEdit,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from .. import export, importer
from ..ledger import SEARCH_FIELDS, SPLIT_CATEGORY, Transaction, transfer_category
from .models import FUTURE_COLOR, keep_colors_when_selected, money, money_color, money_html


def qdate(d):
    return QDate(d.year, d.month, d.day)


def pydate(q):
    return dt.date(q.year(), q.month(), q.day())


def parse_amount(text):
    text = text.strip().replace(",", "").replace("$", "")
    if not text:
        return Decimal("0")
    try:
        return Decimal(text)
    except InvalidOperation:
        raise ValueError(f"Invalid amount: {text!r}")


def money_item(value):
    """Right-aligned, red/green table cell for a monetary amount that sorts numerically."""
    item = SortItem(money(value), float(value))
    item.value = value  # exact amount, for exporting
    item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
    if value:
        item.setForeground(money_color(value))
    return item


def editable_combo(items, current=""):
    cb = QComboBox()
    cb.setEditable(True)
    cb.addItems(items)
    cb.setInsertPolicy(QComboBox.NoInsert)
    comp = QCompleter(items, cb)
    comp.setCaseSensitivity(Qt.CaseInsensitive)
    comp.setFilterMode(Qt.MatchContains)
    cb.setCompleter(comp)
    cb.setCurrentText(current)
    return cb


def category_choices(ledger):
    return ledger.categories() + [transfer_category(a) for a in ledger.accounts()]


# --------------------------------------------------------------------------- transaction


class TransactionDialog(QDialog):
    """Create or edit one transaction. Amount is entered as Payment or Deposit, like Quicken."""

    def __init__(self, ledger, txn=None, account=None, parent=None):
        super().__init__(parent)
        self.ledger = ledger
        self.is_new = txn is None
        self.setWindowTitle("New Transaction" if self.is_new else "Edit Transaction")
        txn = txn or Transaction(account=account or "", date_str=dt.date.today().strftime("%m-%d-%Y"))

        self.account = QComboBox()
        self.account.addItems(ledger.accounts())
        self.account.setCurrentText(txn.account)
        self.date = QDateEdit(qdate(txn.date))
        self.date.setCalendarPopup(True)
        self.date.setDisplayFormat("MM-dd-yyyy")
        self.ref = QLineEdit(txn.ref)
        self.description = editable_combo(ledger.descriptions(), txn.description)
        self.category = editable_combo(category_choices(ledger), txn.category)
        val = QDoubleValidator(0, 1e12, 2)
        val.setNotation(QDoubleValidator.StandardNotation)
        self.payment = QLineEdit(f"{-txn.amount:.2f}" if txn.amount < 0 else "")
        self.deposit = QLineEdit(f"{txn.amount:.2f}" if txn.amount > 0 else "")
        for w in (self.payment, self.deposit):
            w.setValidator(val)
        self.payment.textEdited.connect(lambda s: s and self.deposit.clear())
        self.deposit.textEdited.connect(lambda s: s and self.payment.clear())
        self.memo = QLineEdit(txn.memo)
        self.cleared = QCheckBox("Cleared")
        self.cleared.setChecked(txn.is_cleared)
        self.split_id, self.split, self.tags = txn.split_id, txn.split, txn.tags

        if txn.is_split:
            # The export carries no split detail; keep the {SPLIT} marker untouched.
            self.category.setEnabled(False)
            self.category.setToolTip("Split details are not available in CountAbout format")
        if self.is_new:
            self.description.lineEdit().editingFinished.connect(self._autofill)

        form = QFormLayout()
        form.addRow("Account", self.account)
        form.addRow("Date", self.date)
        form.addRow("Ref / Check #", self.ref)
        form.addRow("Description", self.description)
        form.addRow("Category", self.category)
        amt = QHBoxLayout()
        amt.addWidget(QLabel("Payment"))
        amt.addWidget(self.payment)
        amt.addWidget(QLabel("Deposit"))
        amt.addWidget(self.deposit)
        form.addRow("Amount", amt)
        form.addRow("Memo", self.memo)
        form.addRow("", self.cleared)
        if not self.is_new and ledger.find_transfer_partner(txn) is not None:
            form.addRow("", QLabel("<i>Transfer: the matching entry in the other account will be updated.</i>"))
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        lay = QVBoxLayout(self)
        lay.addLayout(form)
        lay.addWidget(buttons)
        self.resize(520, self.sizeHint().height())

    def _autofill(self):
        """Memorized-payee behavior: fill category and amount from the last use of this description."""
        desc = self.description.currentText()
        if not self.category.currentText():
            self.category.setCurrentText(self.ledger.last_category_for(desc))
        if not self.payment.text() and not self.deposit.text():
            last = max(
                (t for t in self.ledger.transactions if t.description == desc),
                key=lambda t: t.date,
                default=None,
            )
            if last is not None:
                target = self.payment if last.amount < 0 else self.deposit
                target.setText(f"{abs(last.amount):.2f}")

    def result_txn(self):
        t = Transaction(
            account=self.account.currentText(),
            description=self.description.currentText().strip(),
            category=self.category.currentText().strip(),
            split=self.split,
            split_id=self.split_id,
            ref=self.ref.text().strip(),
            memo=self.memo.text().strip(),
            tags=self.tags,
        )
        t.date = pydate(self.date.date())
        t.amount = parse_amount(self.deposit.text()) - parse_amount(self.payment.text())
        t.is_cleared = self.cleared.isChecked()
        return t

    def accept(self):
        try:
            t = self.result_txn()
        except ValueError as e:
            QMessageBox.warning(self, "Invalid entry", str(e))
            return
        problems = []
        if not t.account:
            problems.append("Choose an account.")
        if t.transfer_account == t.account:
            problems.append("A transfer cannot go to the same account.")
        if t.category == SPLIT_CATEGORY and not t.is_split:
            problems.append("New split transactions are not supported by the CountAbout format.")
        tgt = t.transfer_account
        if tgt is not None and tgt not in self.ledger.accounts():
            problems.append(f"Transfer account {tgt!r} does not exist.")
        if problems:
            QMessageBox.warning(self, "Invalid entry", "\n".join(problems))
            return
        super().accept()


# --------------------------------------------------------------------------- import


class ImportDialog(QDialog):
    """Map a bank CSV's columns, preview with auto-categorization and duplicate flags, then import."""

    MAP_FIELDS = [
        ("date_col", "Date column"),
        ("description_col", "Description column"),
        ("amount_col", "Amount column"),
        ("debit_col", "Debit column"),
        ("credit_col", "Credit column"),
        ("memo_col", "Memo column"),
        ("ref_col", "Ref/Check column"),
    ]
    P_INC, P_DATE, P_BANK, P_DESC, P_CAT, P_AMT, P_STATUS = range(7)

    def __init__(self, ledger, account=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Import Bank CSV")
        self.ledger = ledger
        self.header, self.rows, self.candidates = [], [], []
        self.profiles = importer.load_profiles()

        self.path = QLineEdit()
        self.path.setReadOnly(True)
        browse = QPushButton("Browse…")
        browse.clicked.connect(self.browse)
        self.account = QComboBox()
        self.account.addItems(ledger.accounts())
        if account:
            self.account.setCurrentText(account)
        self.profile = QComboBox()
        self.profile.addItem("")
        self.profile.addItems(sorted(self.profiles))
        self.profile.currentTextChanged.connect(self.use_profile)
        save_prof = QPushButton("Save profile…")
        save_prof.clicked.connect(self.save_profile)

        self.col_boxes = {}
        grid = QGridLayout()
        for i, (attr, label) in enumerate(self.MAP_FIELDS):
            cb = QComboBox()
            self.col_boxes[attr] = cb
            grid.addWidget(QLabel(label), i // 2, (i % 2) * 2)
            grid.addWidget(cb, i // 2, (i % 2) * 2 + 1)
        self.date_format = QComboBox()
        self.date_format.setEditable(True)
        self.date_format.addItems([""] + importer.DATE_FORMATS)
        self.date_format.setToolTip("strptime format; blank = auto-detect")
        n = len(self.MAP_FIELDS)
        grid.addWidget(QLabel("Date format"), n // 2, (n % 2) * 2)
        grid.addWidget(self.date_format, n // 2, (n % 2) * 2 + 1)
        self.negate = QCheckBox("Flip signs (charges shown as positive)")
        grid.addWidget(self.negate, n // 2 + 1, 0, 1, 4)
        preview_btn = QPushButton("Preview")
        preview_btn.clicked.connect(self.preview)

        self.table = QTableWidget(0, 7)
        keep_colors_when_selected(self.table)
        self.table.setHorizontalHeaderLabels(["Import", "Date", "Bank text", "Description", "Category", "Amount", "Status"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.summary = QLabel()

        top = QHBoxLayout()
        top.addWidget(QLabel("File"))
        top.addWidget(self.path, 1)
        top.addWidget(browse)
        top2 = QHBoxLayout()
        top2.addWidget(QLabel("Into account"))
        top2.addWidget(self.account, 1)
        top2.addWidget(QLabel("Profile"))
        top2.addWidget(self.profile, 1)
        top2.addWidget(save_prof)
        buttons = QDialogButtonBox()
        self.import_btn = buttons.addButton("Import", QDialogButtonBox.AcceptRole)
        buttons.addButton(QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        lay = QVBoxLayout(self)
        lay.addLayout(top)
        lay.addLayout(top2)
        lay.addLayout(grid)
        lay.addWidget(preview_btn)
        lay.addWidget(self.table, 1)
        lay.addWidget(self.summary)
        lay.addWidget(buttons)
        self.resize(1100, 700)

    def browse(self):
        path, _ = QFileDialog.getOpenFileName(self, "Bank CSV", "", "CSV files (*.csv);;All files (*)")
        if not path:
            return
        try:
            self.header, self.rows = importer.read_bank_csv(path)
        except (OSError, UnicodeDecodeError) as e:
            QMessageBox.warning(self, "Import", f"Could not read {path}:\n{e}")
            return
        self.path.setText(path)
        for cb in self.col_boxes.values():
            cb.clear()
            cb.addItems([""] + self.header)
        # Prefer a saved profile whose columns all exist in this file; else guess.
        match = next(
            (n for n, p in self.profiles.items() if p.date_col in self.header and p.description_col in self.header),
            "",
        )
        if match:
            self.profile.setCurrentText(match)
            self.use_profile(match)
        else:
            self.set_profile(importer.guess_profile(self.header))
        self.preview()

    def set_profile(self, p):
        for attr, cb in self.col_boxes.items():
            cb.setCurrentText(getattr(p, attr))
        self.date_format.setCurrentText(p.date_format)
        self.negate.setChecked(p.negate)

    def use_profile(self, name):
        if name in self.profiles:
            self.set_profile(self.profiles[name])

    def current_profile(self):
        p = importer.Profile(name=self.profile.currentText())
        for attr, cb in self.col_boxes.items():
            setattr(p, attr, cb.currentText())
        p.date_format = self.date_format.currentText()
        p.negate = self.negate.isChecked()
        return p

    def save_profile(self):
        name, ok = QInputDialog.getText(self, "Save profile", "Profile name:", text=self.profile.currentText())
        if not ok or not name.strip():
            return
        p = self.current_profile()
        p.name = name.strip()
        self.profiles[p.name] = p
        importer.save_profiles(self.profiles)
        if self.profile.findText(p.name) < 0:
            self.profile.addItem(p.name)
        self.profile.setCurrentText(p.name)

    def preview(self):
        if not self.rows:
            return
        p = self.current_profile()
        if not p.date_col or not p.description_col or not (p.amount_col or p.debit_col or p.credit_col):
            QMessageBox.information(self, "Import", "Map the date, description, and amount (or debit/credit) columns.")
            return
        self.candidates = importer.build_candidates(self.ledger, self.account.currentText(), self.rows, p)
        cats = category_choices(self.ledger)
        self.table.setRowCount(len(self.candidates))
        for r, c in enumerate(self.candidates):
            inc = QTableWidgetItem()
            inc.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled)
            inc.setCheckState(Qt.Checked if c.include else Qt.Unchecked)
            self.table.setItem(r, self.P_INC, inc)
            for col, item in (
                (self.P_DATE, QTableWidgetItem(c.txn.date_str)),
                (self.P_BANK, QTableWidgetItem(c.bank_text)),
                (self.P_AMT, money_item(c.txn.amount)),
            ):
                item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
                self.table.setItem(r, col, item)
            self.table.setItem(r, self.P_DESC, QTableWidgetItem(c.txn.description))
            self.table.setCellWidget(r, self.P_CAT, editable_combo(cats, c.txn.category))
            if c.error:
                status = f"Error: {c.error}"
            elif c.duplicate_of is not None:
                d = c.duplicate_of
                status = f"Likely duplicate of {d.date_str} {d.description} {money(d.amount, signed=True)}"
            else:
                status = "New"
            st = QTableWidgetItem(status)
            st.setFlags(Qt.ItemIsEnabled)
            self.table.setItem(r, self.P_STATUS, st)
        self.table.resizeColumnsToContents()
        self.table.setColumnWidth(self.P_CAT, 200)
        new = sum(c.include for c in self.candidates)
        dup = sum(c.duplicate_of is not None for c in self.candidates)
        err = sum(bool(c.error) for c in self.candidates)
        self.summary.setText(f"{len(self.candidates)} rows: {new} new, {dup} likely duplicates, {err} errors")

    def accept(self):
        if not self.candidates:
            QMessageBox.information(self, "Import", "Nothing to import. Choose a file and preview first.")
            return
        for r, c in enumerate(self.candidates):
            c.include = self.table.item(r, self.P_INC).checkState() == Qt.Checked and not c.error
            c.txn.description = self.table.item(r, self.P_DESC).text().strip()
            c.txn.category = self.table.cellWidget(r, self.P_CAT).currentText().strip()
            tgt = c.txn.transfer_account
            if c.include and tgt is not None and (tgt not in self.ledger.accounts() or tgt == c.txn.account):
                QMessageBox.warning(self, "Import", f"Row {r + 1}: invalid transfer account {tgt!r}.")
                return
        self.imported = importer.apply_candidates(self.ledger, self.candidates)
        super().accept()


# --------------------------------------------------------------------------- reconcile


class ReconcileDialog(QDialog):
    """Tick off uncleared transactions until the cleared balance matches the statement."""

    def __init__(self, ledger, account, parent=None):
        super().__init__(parent)
        self.ledger, self.account = ledger, account
        self.setWindowTitle(f"Reconcile {account}")
        self.stmt_date = QDateEdit(QDate.currentDate())
        self.stmt_date.setCalendarPopup(True)
        self.stmt_date.setDisplayFormat("MM-dd-yyyy")
        self.stmt_date.dateChanged.connect(self.populate)
        self.stmt_bal = QLineEdit()
        self.stmt_bal.setPlaceholderText("e.g. 1234.56 (owed on a card = negative)")
        self.stmt_bal.textChanged.connect(self.update_totals)
        self.list = QTableWidget(0, 5)
        self.list.setHorizontalHeaderLabels(["", "Date", "Amount", "Description", "Category"])
        self.list.verticalHeader().hide()
        self.list.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.list.setSelectionMode(QAbstractItemView.NoSelection)
        self.list.horizontalHeader().setStretchLastSection(True)
        self.list.itemChanged.connect(self.update_totals)
        self.totals = QLabel()
        mark_all = QPushButton("Mark all")
        mark_all.clicked.connect(lambda: self._set_all(Qt.Checked))
        mark_none = QPushButton("Mark none")
        mark_none.clicked.connect(lambda: self._set_all(Qt.Unchecked))

        form = QFormLayout()
        form.addRow("Statement ending date", self.stmt_date)
        form.addRow("Statement ending balance", self.stmt_bal)
        btnrow = QHBoxLayout()
        btnrow.addWidget(mark_all)
        btnrow.addWidget(mark_none)
        btnrow.addStretch()
        buttons = QDialogButtonBox()
        buttons.addButton("Finish", QDialogButtonBox.AcceptRole)
        buttons.addButton(QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        lay = QVBoxLayout(self)
        lay.addLayout(form)
        lay.addWidget(QLabel("Uncleared transactions through the statement date:"))
        lay.addWidget(self.list, 1)
        lay.addLayout(btnrow)
        lay.addWidget(self.totals)
        lay.addWidget(buttons)
        self.resize(700, 600)
        self.populate()

    def populate(self):
        end = pydate(self.stmt_date.date())
        self.list.blockSignals(True)
        self.txns = sorted(
            (t for t in self.ledger.for_account(self.account) if not t.is_cleared and t.date <= end),
            key=lambda t: t.date,
        )
        self.list.setRowCount(len(self.txns))
        for r, t in enumerate(self.txns):
            check = QTableWidgetItem()
            check.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled)
            check.setCheckState(Qt.Unchecked)
            self.list.setItem(r, 0, check)
            self.list.setItem(r, 1, QTableWidgetItem(t.date_str))
            self.list.setItem(r, 2, money_item(t.amount))
            self.list.setItem(r, 3, QTableWidgetItem(t.description))
            self.list.setItem(r, 4, QTableWidgetItem(t.category))
        self.list.resizeColumnsToContents()
        self.list.blockSignals(False)
        self.update_totals()

    def _set_all(self, state):
        self.list.blockSignals(True)
        for r in range(self.list.rowCount()):
            self.list.item(r, 0).setCheckState(state)
        self.list.blockSignals(False)
        self.update_totals()

    def checked(self):
        return [t for r, t in enumerate(self.txns) if self.list.item(r, 0).checkState() == Qt.Checked]

    def cleared_total(self):
        return self.ledger.balance(self.account, cleared_only=True) + sum((t.amount for t in self.checked()), Decimal("0"))

    def update_totals(self):
        cleared = self.cleared_total()
        try:
            stmt = parse_amount(self.stmt_bal.text())
            diff = f"Difference: {money_html(stmt - cleared)}"
        except ValueError:
            diff = "Difference: (invalid statement balance)"
        self.totals.setText(f"Cleared balance: {money_html(cleared)}&nbsp;&nbsp;&nbsp;&nbsp;{diff}")

    def accept(self):
        try:
            diff = parse_amount(self.stmt_bal.text()) - self.cleared_total()
        except ValueError:
            diff = None
        if diff != 0:
            ok = QMessageBox.question(
                self, "Reconcile", "The cleared balance does not match the statement. Mark the checked items cleared anyway?"
            )
            if ok != QMessageBox.Yes:
                return
        for t in self.checked():
            self.ledger.set_cleared(t, True)
        super().accept()


# --------------------------------------------------------------------------- reports


TOTAL_PREFIX = "— "  # marks summary rows in the category report


class SortItem(QTableWidgetItem):
    """Table cell that shows formatted text but sorts by a separate key.
    (QTableWidgetItem stores DisplayRole and EditRole in one slot, so they can't differ.)"""

    def __init__(self, text, key):
        super().__init__(text)
        self.key = key

    def __lt__(self, other):
        a, b = self.key, getattr(other, "key", other.text())
        if type(a) is not type(b):  # e.g. blank count cells in the total rows
            return str(a) < str(b)
        return a < b


class ReportDialog(QDialog):
    """Category totals or month-by-category totals. Transfers are excluded unless asked for."""

    MODES = ["Spending & income by category", "Category by month", "Income vs. expense by month"]

    def __init__(self, ledger, parent=None):
        super().__init__(parent)
        self.ledger = ledger
        self.setWindowTitle("Reports")
        today = dt.date.today()
        self.start = QDateEdit(QDate(today.year, 1, 1))
        self.end = QDateEdit(qdate(today))
        for w in (self.start, self.end):
            w.setCalendarPopup(True)
            w.setDisplayFormat("MM-dd-yyyy")
        self.mode = QComboBox()
        self.mode.addItems(self.MODES)
        self.accounts = QListWidget()
        self.accounts.setSelectionMode(QAbstractItemView.NoSelection)
        for a in ledger.accounts():
            item = QListWidgetItem(a)
            item.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled)
            item.setCheckState(Qt.Checked)
            self.accounts.addItem(item)
        # Fit the widest account name (scales with the chosen font).
        acct_width = (
            self.accounts.sizeHintForColumn(0)
            + self.accounts.verticalScrollBar().sizeHint().width()
            + 2 * self.accounts.frameWidth()
            + 8
        )
        self.accounts.setFixedWidth(acct_width)
        toggle_all = QPushButton("Accounts All/None")
        toggle_all.setMaximumWidth(acct_width)
        toggle_all.clicked.connect(self.toggle_accounts)
        self.cat_regex = QLineEdit()
        self.cat_regex.setPlaceholderText("all categories")
        self.cat_regex.setToolTip(
            "Python regular expression, case-insensitive, matched anywhere in the category name.\n"
            "Use ^ and $ to anchor, | for alternatives, e.g.  ^(Groceries|Dining)$"
        )
        self.cat_regex.setMinimumWidth(200)
        self.cat_regex.returnPressed.connect(self.run)
        self.cat_regex.setClearButtonEnabled(True)
        # Rerun when cleared so the table never shows filtered results under an empty field.
        self.cat_regex.textChanged.connect(lambda s: s or self.run())
        self.regex_error = QLabel()
        self.regex_error.setStyleSheet("color: rgb(190, 30, 30);")
        run = QPushButton("Run")
        run.clicked.connect(self.run)
        show_all = QPushButton("Show all categories")
        show_all.setToolTip("Clear the category selection and regular expression, then rerun")
        show_all.clicked.connect(self.show_all_categories)
        save = QPushButton("Save…")
        save.setToolTip("Save the report as CSV, Markdown, or PDF")
        save.clicked.connect(self.save_report)
        self.cat_selection = None  # set of category labels picked from the table, or None for all
        self.selection_note = QLabel()
        self.include_transfers = QCheckBox("Include transfers")
        self.include_transfers.setToolTip(
            "Count transfers between accounts, under a category named for the other account.\n"
            "A transfer between two checked accounts appears on both sides and nets to zero."
        )
        self.include_transfers.toggled.connect(self.run)
        self.table = QTableWidget()
        keep_colors_when_selected(self.table)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSortingEnabled(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.table.setToolTip("Ctrl/Shift-click categories, then Run to report only those")

        controls = QHBoxLayout()
        for label, w in (
            ("From", self.start),
            ("To", self.end),
            ("Report", self.mode),
            ("Categories", self.cat_regex),
        ):
            controls.addWidget(QLabel(label))
            controls.addWidget(w)
        controls.addWidget(self.include_transfers)
        controls.addWidget(run)
        controls.addWidget(show_all)
        controls.addStretch()
        controls.addWidget(save)
        notes = QHBoxLayout()
        notes.addWidget(self.selection_note)
        notes.addWidget(self.regex_error)
        notes.addStretch()
        acct_col = QVBoxLayout()
        acct_col.addWidget(toggle_all)
        acct_col.addWidget(self.accounts, 1)
        body = QHBoxLayout()
        body.addLayout(acct_col)
        body.addWidget(self.table, 1)
        lay = QVBoxLayout(self)
        lay.addLayout(controls)
        lay.addLayout(notes)
        lay.addLayout(body, 1)
        self.resize(1100, 700)
        self.run()

    def toggle_accounts(self):
        """Uncheck all accounts if all are checked; otherwise check all."""
        items = [self.accounts.item(i) for i in range(self.accounts.count())]
        state = Qt.Unchecked if all(i.checkState() == Qt.Checked for i in items) else Qt.Checked
        for i in items:
            i.setCheckState(state)

    def selected_txns(self):
        accts = {self.accounts.item(i).text() for i in range(self.accounts.count()) if self.accounts.item(i).checkState() == Qt.Checked}
        lo, hi = pydate(self.start.date()), pydate(self.end.date())
        pattern = self.category_pattern()
        return [
            t
            for t in self.ledger.transactions
            if t.account in accts
            and lo <= t.date <= hi
            and (t.transfer_account is None or self.include_transfers.isChecked())
            and (pattern is None or pattern.search(self.cat_name(t)))
            and (self.cat_selection is None or self.cat_name(t) in self.cat_selection)
        ]

    def picked_categories(self):
        """Category labels of the selected table rows (ignores the total rows and month-only reports)."""
        if self.table.columnCount() == 0 or self.table.horizontalHeaderItem(0).text() != "Category":
            return set()
        rows = {i.row() for i in self.table.selectedIndexes()}
        labels = {self.table.item(r, 0).text() for r in rows}
        return {s for s in labels if not s.startswith(TOTAL_PREFIX)}

    def show_all_categories(self):
        self.cat_selection = None
        self.table.clearSelection()
        self.cat_regex.blockSignals(True)  # avoid the clear-triggered rerun; we run once below
        self.cat_regex.clear()
        self.cat_regex.blockSignals(False)
        self.run()

    def category_pattern(self):
        """Compiled category regex, or None for no filtering. An invalid regex is reported
        and ignored so the report still runs."""
        text = self.cat_regex.text()
        self.regex_error.clear()
        if not text:
            return None
        try:
            return re.compile(text, re.IGNORECASE)
        except re.error as e:
            self.regex_error.setText(f"Invalid regex ({e}); ignoring it")
            return None

    @staticmethod
    def cat_name(t):
        if t.is_split:
            return "(split – details unavailable)"
        if t.transfer_account is not None:
            return f"Transfer: {t.transfer_account}"
        return t.category or "(uncategorized)"

    def report_notes(self):
        """Description of the report's settings, for saved files."""
        accts = [self.accounts.item(i).text() for i in range(self.accounts.count())]
        checked = [a for i, a in enumerate(accts) if self.accounts.item(i).checkState() == Qt.Checked]
        notes = [
            f"Dates: {self.start.date().toString('MM-dd-yyyy')} to {self.end.date().toString('MM-dd-yyyy')}",
            "Accounts: " + ("all" if len(checked) == len(accts) else ", ".join(checked) or "none"),
        ]
        if self.cat_regex.text() and not self.regex_error.text():
            notes.append(f"Categories matching: {self.cat_regex.text()}")
        if self.cat_selection is not None:
            notes.append("Only categories: " + ", ".join(sorted(self.cat_selection, key=str.lower)))
        notes.append("Transfers included" if self.include_transfers.isChecked() else "Transfers excluded")
        return notes

    def table_contents(self):
        """Headers and rows as shown (in the current sort order); amounts as Decimal."""
        t = self.table
        headers = [t.horizontalHeaderItem(c).text() for c in range(t.columnCount())]
        rows = []
        for r in range(t.rowCount()):
            row = []
            for c in range(t.columnCount()):
                item = t.item(r, c)
                if item is None:
                    row.append("")
                elif hasattr(item, "value"):
                    row.append(item.value)
                else:  # counts sort (and export) as int
                    row.append(item.key if isinstance(getattr(item, "key", None), int) else item.text())
            rows.append(row)
        return headers, rows

    def save_report(self):
        path, chosen = QFileDialog.getSaveFileName(
            self, "Save report", "", "CSV (*.csv);;Markdown (*.md);;PDF (*.pdf)"
        )
        if not path:
            return
        ext = os.path.splitext(path)[1].lower()
        if ext not in (".csv", ".md", ".pdf"):
            ext = "." + chosen.split("*.")[1].rstrip(")")
            path += ext
        title = self.mode.currentText()
        notes = self.report_notes()
        headers, rows = self.table_contents()
        try:
            if ext == ".pdf":
                # Month-by-category tables are wide: landscape and a smaller font so columns fit.
                wide = len(headers) > 4
                page = export.to_html(title, notes, headers, rows, TOTAL_PREFIX, font_pt=7 if wide else 9)
                self.write_pdf(path, page, landscape=wide)
            else:
                text = export.to_csv(headers, rows) if ext == ".csv" else export.to_markdown(title, notes, headers, rows)
                with open(path, "w", newline="") as f:
                    f.write(text)
        except OSError as e:
            QMessageBox.warning(self, "Save report", f"Could not save {path}:\n{e}")

    @staticmethod
    def write_pdf(path, html_text, landscape=False):
        open(path, "wb").close()  # QPdfWriter fails silently; this raises OSError if unwritable
        writer = QPdfWriter(path)
        writer.setPageSize(QPageSize(QPageSize.Letter))
        writer.setPageOrientation(QPageLayout.Landscape if landscape else QPageLayout.Portrait)
        writer.setPageMargins(QMarginsF(12, 12, 12, 12), QPageLayout.Millimeter)
        doc = QTextDocument()
        doc.setHtml(html_text)
        doc.print_(writer)

    def fill(self, headers, rows):
        self.table.setSortingEnabled(False)
        self.table.clear()
        self.table.setColumnCount(len(headers))
        self.table.setHorizontalHeaderLabels(headers)
        self.table.setRowCount(len(rows))
        for r, row in enumerate(rows):
            for c, v in enumerate(row):
                if isinstance(v, Decimal):
                    item = money_item(v)
                else:
                    item = SortItem(str(v), v if isinstance(v, int) else str(v).lower())
                self.table.setItem(r, c, item)
        self.table.resizeColumnsToContents()
        self.table.setSortingEnabled(True)

    def run(self):
        picked = self.picked_categories()
        if picked:
            self.cat_selection = picked
        if self.cat_selection is None:
            self.selection_note.clear()
        else:
            self.selection_note.setText(f"<b>Only {len(self.cat_selection)} selected categories</b>")
            self.selection_note.setToolTip("\n".join(sorted(self.cat_selection, key=str.lower)))
        txns = self.selected_txns()
        zero = Decimal("0")
        mode = self.mode.currentIndex()
        if mode == 0:
            totals, counts = defaultdict(lambda: zero), defaultdict(int)
            for t in txns:
                totals[self.cat_name(t)] += t.amount
                counts[self.cat_name(t)] += 1
            rows = sorted(([k, v, counts[k]] for k, v in totals.items()), key=lambda r: r[1])
            inc = sum((v for v in totals.values() if v > 0), zero)
            exp = sum((v for v in totals.values() if v < 0), zero)
            rows += [
                [TOTAL_PREFIX + "Total income", inc, ""],
                [TOTAL_PREFIX + "Total expense", exp, ""],
                [TOTAL_PREFIX + "Net", inc + exp, ""],
            ]
            self.fill(["Category", "Total", "Count"], rows)
        elif mode == 1:
            months = sorted({t.date.strftime("%Y-%m") for t in txns})
            grid = defaultdict(lambda: defaultdict(lambda: zero))
            for t in txns:
                grid[self.cat_name(t)][t.date.strftime("%Y-%m")] += t.amount
            rows = [
                [cat] + [grid[cat][m] for m in months] + [sum(grid[cat].values(), zero)]
                for cat in sorted(grid, key=str.lower)
            ]
            self.fill(["Category"] + months + ["Total"], rows)
        else:
            inc, exp = defaultdict(lambda: zero), defaultdict(lambda: zero)
            for t in txns:
                m = t.date.strftime("%Y-%m")
                (inc if t.amount > 0 else exp)[m] += t.amount
            months = sorted(set(inc) | set(exp))
            self.fill(["Month", "Income", "Expense", "Net"], [[m, inc[m], exp[m], inc[m] + exp[m]] for m in months])


class ReplaceDialog(QDialog):
    """Find a regex in transaction text fields and optionally replace it."""

    C_CHECK, C_DATE, C_ACCOUNT, C_FIELD, C_OLD, C_NEW = range(6)

    def __init__(self, ledger, accounts=None, parent=None):
        super().__init__(parent)
        self.ledger = ledger
        self.replaced = 0
        self.matches = []
        self.setWindowTitle("Search and replace")
        self.find = QLineEdit()
        self.find.setPlaceholderText("regular expression")
        self.find.setToolTip("Python regular expression, matched anywhere in the field")
        self.find.returnPressed.connect(self.search)
        self.do_replace = QCheckBox("Replace with")
        self.do_replace.toggled.connect(self.search)
        self.replace = QLineEdit()
        self.replace.setToolTip("Replacement text; \\1 or \\g<name> insert matched groups, \\g<0> the whole match")
        self.replace.setEnabled(False)
        self.do_replace.toggled.connect(self.replace.setEnabled)
        self.replace.returnPressed.connect(self.search)
        self.case = QCheckBox("Case sensitive")
        self.case.toggled.connect(self.search)
        self.fields = {}
        field_row = QHBoxLayout()
        field_row.addWidget(QLabel("Fields:"))
        for name, attr in SEARCH_FIELDS.items():
            cb = QCheckBox(name)
            cb.setChecked(attr in ("description", "category", "memo"))
            cb.toggled.connect(self.search)
            self.fields[attr] = cb
            field_row.addWidget(cb)
        field_row.addStretch()
        self.accounts = set(accounts) if accounts else None
        self.only_selected = QCheckBox(f"Only {', '.join(accounts)}" if accounts else "All accounts")
        self.only_selected.setChecked(bool(accounts))
        self.only_selected.setEnabled(bool(accounts))
        self.only_selected.toggled.connect(self.search)
        field_row.addWidget(self.only_selected)
        field_row.addWidget(self.case)

        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(["", "Date", "Account", "Field", "Current", "After"])
        self.table.verticalHeader().hide()
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.status = QLabel()
        find_btn = QPushButton("Find")
        find_btn.clicked.connect(self.search)
        self.toggle_btn = QPushButton("Select All/None")
        self.toggle_btn.setToolTip("Uncheck every replaceable match if all are checked; otherwise check them all")
        self.toggle_btn.clicked.connect(self.toggle_all)
        self.apply_btn = QPushButton("Replace checked")
        self.apply_btn.clicked.connect(self.apply)
        close = QPushButton("Close")
        close.clicked.connect(self.accept)

        form = QGridLayout()
        form.addWidget(QLabel("Find"), 0, 0)
        form.addWidget(self.find, 0, 1)
        form.addWidget(find_btn, 0, 2)
        form.addWidget(self.do_replace, 1, 0)
        form.addWidget(self.replace, 1, 1)
        btnrow = QHBoxLayout()
        btnrow.addWidget(self.toggle_btn)
        btnrow.addWidget(self.status, 1)
        btnrow.addWidget(self.apply_btn)
        btnrow.addWidget(close)
        lay = QVBoxLayout(self)
        lay.addLayout(form)
        lay.addLayout(field_row)
        lay.addWidget(self.table, 1)
        lay.addLayout(btnrow)
        self.resize(1000, 600)
        self.search()

    def search(self):
        replacing = self.do_replace.isChecked()
        for w in (self.toggle_btn, self.apply_btn):
            w.setEnabled(replacing)
        self.matches = []
        text = self.find.text()
        if not text:
            self.fill(replacing, "Enter a regular expression to search for.")
            return
        try:
            pat = re.compile(text, 0 if self.case.isChecked() else re.IGNORECASE)
        except re.error as e:
            self.fill(replacing, f"Invalid regex: {e}", error=True)
            return
        attrs = [a for a, cb in self.fields.items() if cb.isChecked()]
        accounts = self.accounts if self.only_selected.isChecked() else None
        try:
            self.matches = self.ledger.search(pat, attrs, self.replace.text() if replacing else None, accounts)
        except (re.error, IndexError) as e:  # bad group reference in the replacement
            self.fill(replacing, f"Invalid replacement: {e}", error=True)
            return
        self.fill(replacing)

    def fill(self, replacing, message=None, error=False):
        """Show self.matches, and `message` (or a match count) in the status line."""
        names = {a: n for n, a in SEARCH_FIELDS.items()}
        self.table.setRowCount(len(self.matches))
        self.table.setColumnHidden(self.C_CHECK, not replacing)
        self.table.setColumnHidden(self.C_NEW, not replacing)
        blocked = 0
        for r, (t, attr, old, new) in enumerate(self.matches):
            problem = self.ledger.replacement_problem(attr, old, new) if replacing else None
            check = QTableWidgetItem()
            if problem:
                blocked += 1
                check.setFlags(Qt.NoItemFlags)
                check.setCheckState(Qt.Unchecked)
                check.setToolTip(f"Can't replace: {problem}")
            else:
                check.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled)
                check.setCheckState(Qt.Checked)
            self.table.setItem(r, self.C_CHECK, check)
            for c, text in (
                (self.C_DATE, t.date_str),
                (self.C_ACCOUNT, t.account),
                (self.C_FIELD, names[attr]),
                (self.C_OLD, old),
                (self.C_NEW, f"({problem})" if problem else new or ""),
            ):
                self.table.setItem(r, c, QTableWidgetItem(text))
            if problem:
                self.table.item(r, self.C_NEW).setForeground(FUTURE_COLOR)
        self.table.resizeColumnsToContents()
        if message is None:
            txns = len({id(m[0]) for m in self.matches})
            message = f"{len(self.matches)} matches in {txns} transactions"
            if blocked:
                message += f"; {blocked} can't be replaced (hover the checkbox for why)"
        self.status.setStyleSheet("color: rgb(190, 30, 30);" if error else "")
        self.status.setText(message)

    def toggle_all(self):
        """Uncheck all replaceable matches if all are checked; otherwise check them all.
        Matches that can't be replaced stay unchecked."""
        items = [self.table.item(r, self.C_CHECK) for r in range(self.table.rowCount())]
        items = [i for i in items if i.flags() & Qt.ItemIsUserCheckable]
        state = Qt.Unchecked if all(i.checkState() == Qt.Checked for i in items) else Qt.Checked
        for i in items:
            i.setCheckState(state)

    def apply(self):
        changes = [
            (t, attr, new)
            for r, (t, attr, old, new) in enumerate(self.matches)
            if self.table.item(r, self.C_CHECK).checkState() == Qt.Checked
        ]
        if not changes:
            return
        n = len({id(t) for t, _, _ in changes})
        msg = f"Replace {len(changes)} matches in {n} transactions?\n\nThe ledger file isn't changed until you save."
        if QMessageBox.question(self, "Search and replace", msg) != QMessageBox.Yes:
            return
        self.ledger.apply_replacements(changes)
        self.replaced += len(changes)
        self.search()  # show what still matches
