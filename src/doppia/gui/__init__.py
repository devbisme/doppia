"""Main window and entry point."""

import datetime as dt
import json
import os
import sys
from collections import Counter
from decimal import Decimal

from PySide6.QtCore import QItemSelection, QItemSelectionModel, QSettings, QSize, Qt, QTimer
from PySide6.QtGui import QAction, QFont, QFontMetrics, QKeySequence
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QFileDialog,
    QFontDialog,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMessageBox,
    QSplitter,
    QStyle,
    QTableView,
    QToolButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..ledger import Ledger
from .dialogs import (
    ClipboardImportDialog,
    ImportDialog,
    ReconcileDialog,
    ReplaceDialog,
    ReportDialog,
    TransactionDialog,
    parse_amount,
    sign_icon,
)
from .models import (
    NEG_COLOR,
    POS_COLOR,
    PaddedDelegate,
    RegisterFilter,
    RegisterModel,
    even_widths,
    fit_widths,
    keep_colors_when_selected,
    money,
    money_color,
    untruncated_width,
)

ALL_ACCOUNTS = "All accounts"
TITLE_MAX = 30  # longest account list shown over the register, including the "..."


class MainWindow(QMainWindow):
    def __init__(self, path=None):
        super().__init__()
        self.settings = QSettings("doppia", "doppia")
        self.ledger = None
        self.balances = {}  # {account: (balance as of today, cleared balance)}
        self.accounts = []  # selected account names; empty means all accounts

        # Account list: name, balance as of today, cleared balance.
        self.tree = QTreeWidget()
        keep_colors_when_selected(self.tree)
        self.tree.setHeaderLabels(["Account", "Balance", "Cleared"])
        for col in (1, 2):  # right-aligned like the amounts below them
            self.tree.headerItem().setTextAlignment(col, Qt.AlignRight | Qt.AlignVCenter)
        self.tree.setRootIsDecorated(False)
        # Ctrl/Shift-click selects several accounts; the register shows their combined transactions.
        self.tree.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.tree.itemSelectionChanged.connect(self.account_selected)
        self.tree.header().setSectionResizeMode(0, QHeaderView.Stretch)
        self.tree.header().setStretchLastSection(False)
        for col in (1, 2):
            self.tree.header().setSectionResizeMode(col, QHeaderView.ResizeToContents)
        # Widen the right-aligned Cleared column by 2 characters to separate it from Balance.
        self.tree.setItemDelegateForColumn(2, PaddedDelegate(2, self.tree))

        # Register.
        self.model = RegisterModel(None)
        self.proxy = RegisterFilter()
        self.proxy.setSourceModel(self.model)
        self.view = QTableView()
        keep_colors_when_selected(self.view)
        self.view.setModel(self.proxy)
        self.view.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.view.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.view.verticalHeader().hide()
        self.view.selectionModel().selectionChanged.connect(self.select_whole_splits)
        self.view.clicked.connect(self.on_click)
        self.view.doubleClicked.connect(self.on_double_click)
        self.view.setContextMenuPolicy(Qt.CustomContextMenu)
        self.view.customContextMenuRequested.connect(self.context_menu)
        hdr = self.view.horizontalHeader()
        hdr.setSectionsMovable(True)
        hdr.setSectionResizeMode(QHeaderView.Interactive)
        hdr.setSectionResizeMode(RegisterModel.C_CLR, QHeaderView.Fixed)
        hdr.setMinimumSectionSize(1)  # the default is wider than the snug Clr column
        # Header clicks sort; clicking the same header again reverses the order.
        self.view.setSortingEnabled(True)
        self.view.sortByColumn(RegisterModel.C_DATE, Qt.DescendingOrder)

        self.restore_column_order()
        self.columns_fitted = False
        self.model.modelReset.connect(self.size_fixed_columns)

        self.search = QLineEdit()
        self.search.setPlaceholderText("Filter (regular expression)…")
        self.filter_help = (
            "Case-insensitive Python regular expression, matched in the description, category,\n"
            "memo, ref, date, or amount. Escape special characters, e.g. \\$3,000 or 12\\.50"
        )
        self.search.setToolTip(self.filter_help)
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self.filter_changed)
        self.title = QLabel()
        self.hidden_note = QLabel()
        self.hidden_note.setStyleSheet("color: rgb(180, 100, 0);")
        # Filtering can emit thousands of row signals; update once after they've all arrived.
        self.note_timer = QTimer(self)
        self.note_timer.setSingleShot(True)
        self.note_timer.setInterval(0)
        self.note_timer.timeout.connect(self.update_hidden_note)
        for signal in (self.proxy.modelReset, self.proxy.rowsInserted, self.proxy.rowsRemoved):
            signal.connect(self.note_timer.start)
        self.txn_btns = []
        for icon, tip, slot in (
            (sign_icon(POS_COLOR, plus=True), "New transaction", self.new_txn),
            (sign_icon(NEG_COLOR, plus=False), "Delete transactions", self.delete_txn),
        ):
            b = QToolButton()
            b.setIcon(icon)
            b.setToolTip(tip)
            b.setAccessibleName(tip)
            b.clicked.connect(slot)
            self.txn_btns.append(b)

        bar = QHBoxLayout()
        bar.addWidget(self.title)
        bar.addWidget(self.hidden_note)
        bar.addStretch()
        bar.addWidget(self.search, 1)
        for b in self.txn_btns:
            bar.addWidget(b)
        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.addLayout(bar)
        bar.addSpacing(bar.spacing())  # same gap after the last button as between the buttons
        rl.addWidget(self.view)

        self.split = QSplitter()
        self.split.addWidget(self.tree)
        self.split.addWidget(right)
        self.split.setSizes([330, 1100])
        self.setCentralWidget(self.split)
        self.build_menus()
        self.resize(1450, 850)
        geometry = self.settings.value("geometry")
        if geometry:
            self.restoreGeometry(geometry)

        self.default_font = QFont(QApplication.font())
        saved, desc = QFont(), self.settings.value("font")
        self.apply_font(saved if desc and saved.fromString(desc) else self.default_font)

        self.show_ledger(Ledger())
        if path:
            self.open_path(path)
        self.update_title()

    # ------------------------------------------------------------------ menus

    def build_menus(self):
        def act(menu, text, slot, shortcut=None):
            a = QAction(text, self)
            a.triggered.connect(slot)
            if shortcut:
                a.setShortcut(QKeySequence(shortcut))
            menu.addAction(a)
            return a

        m = self.menuBar().addMenu("&File")
        act(m, "&New", self.new_file, "Ctrl+Shift+N")  # Ctrl+N is a new transaction
        act(m, "&Open…", self.open_file, QKeySequence.Open)
        act(m, "&Save", self.save, QKeySequence.Save)
        act(m, "Save &As…", self.save_as, QKeySequence.SaveAs)
        m.addSeparator()
        act(m, "&Import bank CSV…", self.import_csv, "Ctrl+I")
        act(m, "Import from &clipboard (local LLM)…", self.import_clipboard, "Ctrl+Shift+V")
        act(m, "&Export for CountAbout…", self.export_countabout)
        m.addSeparator()
        act(m, "&Quit", self.close, QKeySequence.Quit)

        m = self.menuBar().addMenu("&Transaction")
        act(m, "&New", self.new_txn, QKeySequence.New)
        act(m, "&Edit", self.edit_txn, "Ctrl+E")
        act(m, "&Delete", self.delete_txn, "Ctrl+Del")
        act(m, "Toggle &cleared", self.toggle_cleared, "Ctrl+K")
        m.addSeparator()
        act(m, "&Search and replace…", self.search_replace, "Ctrl+H")

        m = self.menuBar().addMenu("&Account")
        act(m, "&New account…", self.new_account)
        act(m, "&Rename account…", self.rename_account)
        act(m, "Re&concile…", self.reconcile, "Ctrl+R")

        m = self.menuBar().addMenu("&Reports")
        act(m, "&Reports…", self.reports, "Ctrl+P")
        act(m, "Rename &category…", self.rename_category)

        m = self.menuBar().addMenu("&View")
        act(m, "&Hide cleared", self.proxy.set_hide_cleared).setCheckable(True)
        act(m, "Hide f&uture", self.proxy.set_hide_future).setCheckable(True)
        act(m, "Resize &columns to fit", self.resize_columns)
        act(m, "Reset column &order", self.reset_column_order)
        m.addSeparator()
        act(m, "&Font…", self.choose_font)
        act(m, "&Reset font", self.reset_font)

    # ------------------------------------------------------------------ font

    # Columns narrowed, in this order, when the register doesn't fit its pane.
    SHRINK_ORDER = (RegisterModel.C_MEMO, RegisterModel.C_DESC, RegisterModel.C_CAT, RegisterModel.C_ACCOUNT)
    # The rest always show their entries in full.
    FIXED_COLUMNS = (
        RegisterModel.C_DATE,
        RegisterModel.C_REF,
        RegisterModel.C_AMOUNT,
        RegisterModel.C_CLR,
        RegisterModel.C_BAL,
    )

    def choose_font(self):
        ok, font = QFontDialog.getFont(QApplication.font(), self, "Font")
        if ok:
            self.apply_font(font)
            self.settings.setValue("font", font.toString())

    def reset_font(self):
        self.apply_font(self.default_font)
        self.settings.remove("font")

    def apply_font(self, font):
        """Set the font for the whole app (including dialogs opened later) and resize
        everything that was sized in pixels."""
        QApplication.setFont(font)
        fm = QFontMetrics(font)
        for b in self.txn_btns:
            b.setIconSize(QSize(fm.height(), fm.height()))
        self.view.verticalHeader().setDefaultSectionSize(fm.height() + 6)
        # The new font reaches widgets via events, so measure after they've been processed.
        QTimer.singleShot(0, lambda: self.size_fixed_columns(grow_only=False))
        title_font = QFont(font)
        title_font.setBold(True)
        if font.pointSizeF() > 0:
            title_font.setPointSizeF(font.pointSizeF() * 1.25)
        else:  # font sized in pixels
            title_font.setPixelSize(round(font.pixelSize() * 1.25))
        self.title.setFont(title_font)
        # Rebuild the account list: its bold/italic rows copied the old font when created.
        self.refresh_accounts(reset_register=False)
        # The new font reaches widgets via events; measure after they've been processed.
        QTimer.singleShot(0, self.fit_account_list)

    def measure_columns(self, columns):
        """{column: (longest entry's width, width cutting off under 5% of entries, header's
        width)} in pixels."""
        fm = self.view.fontMetrics()
        # Cell margins, the grid line, and slack: drawn text can run wider than its metrics,
        # and without room to spare Qt elides it.
        margin = 2 * (self.view.style().pixelMetric(QStyle.PM_FocusFrameHMargin, None, self.view) + 1)
        margin += 1 + fm.averageCharWidth()
        header_font = QFont(self.view.font())
        header_font.setBold(True)  # the sort column's header is bold
        header_fm = QFontMetrics(header_font)
        sizes = {}
        measured = {}  # entries repeat heavily, and measuring text is the slow part
        for col in columns:
            texts = self.model.column_texts(col)
            for s in texts:
                if s and s not in measured:
                    measured[s] = fm.horizontalAdvance(s)
            widths = [measured[s] for s in texts if s]
            if col == RegisterModel.C_CLR:  # just its label, with a small space on each side
                snug = header_fm.horizontalAdvance(RegisterModel.COLS[col]) + 2 * fm.averageCharWidth()
                sizes[col] = (snug, snug, snug)
                continue
            header = header_fm.horizontalAdvance(RegisterModel.COLS[col]) + margin + 20  # 20: sort arrow
            floor = max(untruncated_width(widths) + margin, header)
            sizes[col] = (max(max(widths, default=0) + margin, header), floor, header)
        return sizes

    def initial_columns(self):
        """Use the column widths from the last run, or fit them if this is the first."""
        try:
            saved = json.loads(self.settings.value("column_widths") or "{}")
        except ValueError:
            saved = {}
        if not saved:
            self.fit_columns()
            return
        for col, name in enumerate(RegisterModel.COLS):
            if saved.get(name):
                self.view.setColumnWidth(col, saved[name])
        self.size_fixed_columns()  # the data may have outgrown a saved width

    def restore_column_order(self):
        """Put the columns in their saved order. It is stored by name, so a column added in a
        later version just stays at the end."""
        try:
            saved = [n for n in json.loads(self.settings.value("column_order") or "[]") if n in RegisterModel.COLS]
        except (TypeError, ValueError):
            return
        hdr = self.view.horizontalHeader()
        for position, name in enumerate(saved):
            hdr.moveSection(hdr.visualIndex(RegisterModel.COLS.index(name)), position)

    def reset_column_order(self):
        hdr = self.view.horizontalHeader()
        for col in range(hdr.count()):
            hdr.moveSection(hdr.visualIndex(col), col)

    def save_columns(self):
        hdr = self.view.horizontalHeader()
        order = sorted(RegisterModel.COLS, key=lambda name: hdr.visualIndex(RegisterModel.COLS.index(name)))
        self.settings.setValue("column_order", json.dumps(order))
        if self.columns_fitted:  # before a ledger loads, the widths are only placeholders
            self.view.setColumnHidden(RegisterModel.C_ACCOUNT, False)  # a hidden column reads as 0
            widths = {name: self.view.columnWidth(col) for col, name in enumerate(RegisterModel.COLS)}
            self.settings.setValue("column_widths", json.dumps(widths))

    def fit_columns(self):
        """Size all columns to their contents, narrowing SHRINK_ORDER to fit the pane."""
        sizes = self.measure_columns(range(len(RegisterModel.COLS)))
        widths = {c: s[0] for c, s in sizes.items()}
        floors = {c: s[1] for c, s in sizes.items()}
        for col, width in fit_widths(widths, floors, self.SHRINK_ORDER, self.pane_width()).items():
            self.view.setColumnWidth(col, width)

    def pane_width(self):
        """Width available to the columns, leaving room for the vertical scrollbar."""
        return self.view.width() - 2 * self.view.frameWidth() - self.view.verticalScrollBar().sizeHint().width()

    def resize_columns(self):
        """Change all visible columns by the same amount to fit the pane. The FIXED_COLUMNS stop
        at their contents, and the others at their headers."""
        sizes = self.measure_columns(range(len(RegisterModel.COLS)))
        shown = [c for c in sizes if not self.view.isColumnHidden(c) and c != RegisterModel.C_CLR]
        widths = {c: self.view.columnWidth(c) for c in shown}
        minimums = {c: sizes[c][0 if c in self.FIXED_COLUMNS else 2] for c in shown}
        available = self.pane_width() - self.view.columnWidth(RegisterModel.C_CLR)
        for col, width in even_widths(widths, minimums, available).items():
            self.view.setColumnWidth(col, width)

    def size_fixed_columns(self, grow_only=True):
        """Keep every entry of the FIXED_COLUMNS visible. Normally they only grow, so that
        ordinary edits and account switches don't make the columns jitter."""
        for col, (width, *_) in self.measure_columns(self.FIXED_COLUMNS).items():
            grow = grow_only and col != RegisterModel.C_CLR  # Clr is always exactly snug
            self.view.setColumnWidth(col, max(width, self.view.columnWidth(col)) if grow else width)

    def fit_account_list(self):
        """Widen the account pane so names and balances fit (only on font change or file open,
        so a manually dragged splitter isn't undone by every edit)."""
        t = self.tree
        need = (
            sum(t.sizeHintForColumn(c) for c in range(t.columnCount()))
            + t.verticalScrollBar().sizeHint().width()
            + 2 * t.frameWidth()
            + 12
        )
        total = sum(self.split.sizes())
        need = min(need, total // 2)
        self.split.setSizes([need, total - need])

    # ------------------------------------------------------------------ file

    def open_path(self, path):
        try:
            ledger = Ledger.load(path)
        except (OSError, ValueError) as e:
            QMessageBox.critical(self, "Open", str(e))
            return
        self.show_ledger(ledger)

    def show_ledger(self, ledger):
        self.ledger = ledger
        self.model.set_ledger(ledger)
        self.account = None
        self.refresh_accounts()
        self.fit_account_list()
        self.settle_columns()

    def settle_columns(self):
        """Size the columns once, from the saved widths or else from the first data to show."""
        if self.columns_fitted or not (self.settings.value("column_widths") or self.ledger.transactions):
            return
        self.columns_fitted = True
        QTimer.singleShot(0, self.initial_columns)  # after the window has its final layout

    def maybe_save(self):
        if self.ledger is None or not self.ledger.dirty:
            return True
        r = QMessageBox.question(
            self, "Unsaved changes", "Save changes?", QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel
        )
        if r == QMessageBox.Save:
            return self.save()
        return r == QMessageBox.Discard

    def new_file(self):
        if self.maybe_save():
            self.show_ledger(Ledger())
            self.update_title()

    def open_file(self):
        if not self.maybe_save():
            return
        path, _ = QFileDialog.getOpenFileName(self, "Open ledger", "", "CSV files (*.csv);;All files (*)")
        if path:
            self.open_path(path)
            self.update_title()

    def save(self):
        if self.ledger is None:
            return False
        if not self.ledger.path:
            return self.save_as()
        try:
            self.ledger.save()
        except OSError as e:
            QMessageBox.critical(self, "Save", str(e))
            return False
        self.update_title()
        return True

    def save_as(self):
        if self.ledger is None:
            return False
        path, _ = QFileDialog.getSaveFileName(self, "Save ledger as", self.ledger.path or "", "CSV files (*.csv)")
        if not path:
            return False
        self.ledger.path = path
        return self.save()

    def export_countabout(self):
        """Write a separate CSV in CountAbout's format; the open ledger is not changed."""
        if not self.need_ledger():
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Export for CountAbout (each split becomes one total row)", "countabout-export.csv", "CSV files (*.csv)"
        )
        if not path:
            return
        if self.ledger.path and os.path.abspath(path) == os.path.abspath(self.ledger.path):
            QMessageBox.warning(self, "Export", "Choose a different file: this would replace the open ledger and lose split lines.")
            return
        try:
            with open(path, "w", newline="") as f:
                f.write(self.ledger.to_countabout_text())
        except OSError as e:
            QMessageBox.critical(self, "Export", str(e))
            return
        self.statusBar().showMessage(f"Exported to {path}", 5000)

    def closeEvent(self, ev):
        if self.maybe_save():
            self.settings.setValue("geometry", self.saveGeometry())
            self.save_columns()
            ev.accept()
        else:
            ev.ignore()

    # ------------------------------------------------------------------ view refresh

    @property
    def account(self):
        """The selected account when exactly one is selected, else None."""
        return self.accounts[0] if len(self.accounts) == 1 else None

    @account.setter
    def account(self, name):
        self.accounts = [] if name is None else [name]

    def show_accounts(self):
        """Load the selected accounts into the register."""
        self.update_totals()
        self.model.set_account(self.accounts or None)
        self.view.setColumnHidden(RegisterModel.C_ACCOUNT, self.account is not None)
        self.update_title()

    def update_title(self):
        name = os.path.basename(self.ledger.path) if self.ledger and self.ledger.path else "(no file)"
        dirty = "*" if self.ledger and self.ledger.dirty else ""
        self.setWindowTitle(f"{dirty}{name} — doppia")
        names = ", ".join(self.accounts) or ALL_ACCOUNTS
        short = names if len(names) <= TITLE_MAX else names[: TITLE_MAX - 3] + "..."
        self.title.setText(short)
        self.title.setToolTip(names if short != names else "")

    def update_hidden_note(self):
        """Say so when the View toggles or the filter are hiding transactions."""
        counts = Counter()
        if self.model.rowCount() != self.proxy.rowCount():
            counts = Counter(self.proxy.hidden_reason(t) for t in self.model.entries)
            del counts[None]
        hidden = counts.total()
        self.hidden_note.setText(f"{hidden:,} hidden" if hidden else "")
        # A row hidden for two reasons counts under the first, so the lines add up to the total.
        lines = (
            f"{counts[reason]:,} {label}"
            for reason, label in (("cleared", "cleared"), ("future", "future"), ("filter", "not matching the filter"))
            if counts.get(reason)
        )
        self.hidden_note.setToolTip("\n".join(lines))

    def filter_changed(self, text):
        error = self.proxy.set_text(text)
        # An invalid regex is ignored (all rows shown); tint the field and say why.
        self.search.setStyleSheet("background: rgb(255, 225, 225);" if error else "")
        self.search.setToolTip(f"Invalid regular expression: {error}" if error else self.filter_help)

    def refresh_accounts(self, reset_register=True):
        """Rebuild the account list, keeping the current selection."""
        self.tree.blockSignals(True)
        self.tree.clear()
        balances = self.ledger.account_balances(as_of=dt.date.today()) if self.ledger else {}
        self.balances = balances
        sel = []
        for name in [ALL_ACCOUNTS] + sorted(balances, key=str.lower):
            if name == ALL_ACCOUNTS:
                item = QTreeWidgetItem([name, "", ""])
                f = item.font(0)
                f.setBold(True)
                item.setFont(0, f)
            else:
                bal, clr = balances[name]
                item = QTreeWidgetItem([name, money(bal), money(clr)])
                for c, v in ((1, bal), (2, clr)):
                    item.setTextAlignment(c, Qt.AlignRight | Qt.AlignVCenter)
                    if v:
                        item.setForeground(c, money_color(v))
            self.tree.addTopLevelItem(item)
            if name in (self.accounts or [ALL_ACCOUNTS]):
                sel.append(item)
        # Totals of the selected accounts; filled in by update_totals().
        self.total_item = QTreeWidgetItem(["Total", "", ""])
        self.total_item.setFlags(Qt.ItemIsEnabled)
        for c in range(3):
            f = self.total_item.font(c)
            f.setBold(True)
            self.total_item.setFont(c, f)
            if c:
                self.total_item.setTextAlignment(c, Qt.AlignRight | Qt.AlignVCenter)
        self.tree.addTopLevelItem(self.total_item)
        if sel:
            self.tree.setCurrentItem(sel[0])
            for item in sel:
                item.setSelected(True)
        self.tree.blockSignals(False)
        if reset_register:
            self.show_accounts()
        else:
            self.update_totals()
            self.update_title()

    def update_totals(self):
        """Sum the balance and cleared columns over the selected accounts (all if none)."""
        zero = Decimal("0")
        names = self.accounts or self.balances
        bal = sum((self.balances[n][0] for n in names if n in self.balances), zero)
        clr = sum((self.balances[n][1] for n in names if n in self.balances), zero)
        for c, v in ((1, bal), (2, clr)):
            self.total_item.setText(c, money(v))
            self.total_item.setForeground(c, money_color(v) or self.tree.palette().text())

    def changed(self, select_txn=None):
        """Call after any ledger mutation."""
        self.refresh_accounts()
        self.settle_columns()
        if select_txn is not None:
            self.select_txn(select_txn)

    def select_txn(self, txn):
        for r, t in enumerate(self.model.rows):
            if t is txn:
                idx = self.proxy.mapFromSource(self.model.index(r, 0))
                if idx.isValid():
                    self.view.selectRow(idx.row())
                    self.view.scrollTo(idx)
                return

    def account_selected(self):
        if self.ledger is None:
            return
        names = {item.text(0) for item in self.tree.selectedItems()}
        if not names:
            return
        # "All accounts" anywhere in the selection means all of them.
        if ALL_ACCOUNTS in names:
            self.accounts = []
        else:
            self.accounts = [a for a in self.ledger.accounts() if a in names]
        self.show_accounts()

    def select_whole_splits(self, selected, deselected):
        """Keep a split highlighted all or not at all. Ctrl-clicking one of its rows
        deselects it; any other change that leaves it partly highlighted selects all of it."""
        if len(self.model.rows) == len(self.model.entries):
            return  # no splits showing
        model = self.view.selectionModel()
        ctrl = QApplication.keyboardModifiers() & Qt.ControlModifier
        select, deselect = QItemSelection(), QItemSelection()
        seen = set()
        for change, target in ((selected, select), (deselected, deselect if ctrl else select)):
            for rng in change:
                for row in range(rng.top(), rng.bottom() + 1):
                    source = self.proxy.mapToSource(self.proxy.index(row, 0)).row()
                    if not self.model.txn(source).lines or self.model.block(source) in seen:
                        continue
                    first, last = self.model.block(source)
                    seen.add((first, last))
                    top = self.proxy.mapFromSource(self.model.index(first, 0)).row()
                    bottom = self.proxy.mapFromSource(self.model.index(last, 0)).row()
                    marked = sum(model.isRowSelected(r) for r in range(top, bottom + 1))
                    if 0 < marked <= bottom - top:  # partly highlighted
                        target.select(self.proxy.index(top, 0), self.proxy.index(bottom, 0))
        if select.count():
            model.select(select, QItemSelectionModel.Select | QItemSelectionModel.Rows)
        if deselect.count():
            model.select(deselect, QItemSelectionModel.Deselect | QItemSelectionModel.Rows)

    def current_txn(self):
        idx = self.view.currentIndex()
        if not idx.isValid():
            return None
        return self.model.txn(self.proxy.mapToSource(idx).row())

    # ------------------------------------------------------------------ transactions

    def need_ledger(self):
        if self.ledger is None:
            QMessageBox.information(self, "doppia", "Open a ledger CSV first (File → Open).")
            return False
        return True

    def new_txn(self):
        if not self.need_ledger():
            return
        # With several accounts selected, default the new transaction to the first of them.
        dlg = TransactionDialog(self.ledger, account=self.accounts[0] if self.accounts else None, parent=self)
        if dlg.exec():
            self.add_from(dlg)

    def add_from(self, dlg):
        """Add the dialog's new transaction or split."""
        lines = dlg.result_lines()
        if lines:
            self.ledger.add_split(lines)
        else:
            lines = [dlg.result_txn()]
            self.ledger.add(lines[0])
        self.changed(lines[0])

    def edit_txn(self):
        t = self.current_txn()
        if t is None:
            return
        dlg = TransactionDialog(self.ledger, txn=t, parent=self)
        if dlg.exec():
            lines = dlg.result_lines()
            if lines or t.lines:
                lines = lines or [dlg.result_txn()]
                self.ledger.update_split(t, lines)
                self.changed(lines[0])
            else:
                self.ledger.update(t, dlg.result_txn())
                self.changed(t)

    def copy_txn(self):
        t = self.current_txn()
        if t is None:
            return
        dlg = TransactionDialog(self.ledger, parent=self, copy_of=t)
        if dlg.exec():
            self.add_from(dlg)

    def on_click(self, idx):
        if idx.column() == RegisterModel.C_CLR:
            # Only the clicked box, even if other rows are highlighted.
            self.toggle_txns([self.model.txn(self.proxy.mapToSource(idx).row())])

    def on_double_click(self, idx):
        if idx.column() != RegisterModel.C_CLR:
            self.edit_txn()

    def context_menu(self, pos):
        idx = self.view.indexAt(pos)
        if not idx.isValid():
            return
        self.view.setCurrentIndex(idx)
        menu = QMenu(self)
        menu.addAction("Edit", self.edit_txn)
        menu.addAction("Copy", self.copy_txn)
        menu.addSeparator()
        menu.addAction("Delete transaction", self.delete_txn)
        menu.exec(self.view.viewport().mapToGlobal(pos))

    def selected_txns(self):
        """Highlighted transactions, or the current one if nothing is highlighted."""
        rows = {i.row() for i in self.view.selectionModel().selectedIndexes()}
        if not rows:
            t = self.current_txn()
            return [t] if t is not None else []
        txns = [self.model.txn(self.proxy.mapToSource(self.proxy.index(r, 0)).row()) for r in sorted(rows)]
        return list({id(t): t for t in txns}.values())  # a split's rows are one transaction

    def delete_txn(self):
        txns = self.selected_txns()
        if len(txns) > 1:
            self.delete_txns(txns)
        elif txns:
            self.delete_one(txns[0])

    def delete_txns(self, txns):
        lines = self.ledger.with_split_lines(txns)
        rows = self.ledger.with_transfer_partners(lines)
        msg = f"Delete {len(txns)} transactions?"
        if len(rows) > len(lines):
            msg += f"\n\n{len(rows) - len(lines)} matching transfer entries in other accounts will also be deleted."
        if QMessageBox.question(self, "Delete", msg, defaultButton=QMessageBox.No) == QMessageBox.Yes:
            row = min(i.row() for i in self.view.selectionModel().selectedIndexes())
            self.ledger.delete_rows(rows)
            self.changed()
            self.view.selectRow(min(row, self.proxy.rowCount() - 1))

    def delete_one(self, t):
        partner = self.ledger.find_transfer_partner(t)
        msg = f"Delete {t.date_str} {t.description} {money(t.amount, signed=True)}?"
        if partner is not None:
            msg += f"\n\nThe matching transfer entry in {partner.account} will also be deleted."
        if QMessageBox.question(self, "Delete", msg) == QMessageBox.Yes:
            row = self.proxy.mapFromSource(self.model.index(self.model.rows.index(t), 0)).row()
            self.ledger.delete(t)
            self.changed()
            self.view.selectRow(min(row, self.proxy.rowCount() - 1))

    def toggle_cleared(self):
        self.toggle_txns(self.selected_txns())

    def toggle_txns(self, txns):
        if not txns:
            return
        for t in txns:
            self.ledger.set_cleared(t, not t.is_cleared)
        # Clearing doesn't change row order or running balances, so update in place
        # rather than resetting the register (which would lose the scroll position).
        self.model.txns_changed(txns)
        self.refresh_accounts(reset_register=False)

    def search_replace(self):
        if not self.need_ledger():
            return
        dlg = ReplaceDialog(self.ledger, self.accounts, self)
        dlg.exec()
        if dlg.replaced:
            self.changed()

    # ------------------------------------------------------------------ accounts

    def new_account(self):
        if not self.need_ledger():
            return
        name, ok = QInputDialog.getText(self, "New account", "Account name:")
        if not ok or not name.strip():
            return
        bal, ok = QInputDialog.getText(self, "New account", "Opening balance (negative for amount owed):", text="0.00")
        if not ok:
            return
        try:
            self.ledger.create_account(name.strip(), parse_amount(bal))
        except ValueError as e:
            QMessageBox.warning(self, "New account", str(e))
            return
        self.account = name.strip()
        self.changed()

    def rename_account(self):
        if not self.need_ledger() or self.account is None:
            return
        new, ok = QInputDialog.getText(self, "Rename account", f"New name for {self.account}:", text=self.account)
        if not ok or not new.strip() or new.strip() == self.account:
            return
        try:
            self.ledger.rename_account(self.account, new.strip())
        except ValueError as e:
            QMessageBox.warning(self, "Rename account", str(e))
            return
        self.account = new.strip()
        self.changed()

    def rename_category(self):
        if not self.need_ledger():
            return
        cats = self.ledger.categories()
        old, ok = QInputDialog.getItem(self, "Rename category", "Category:", cats, 0, False)
        if not ok:
            return
        new, ok = QInputDialog.getText(self, "Rename category", f"Rename {old!r} to (existing name merges):", text=old)
        if ok and new.strip() and new.strip() != old:
            self.ledger.rename_category(old, new.strip())
            self.changed()

    def reconcile(self):
        if not self.need_ledger():
            return
        if self.account is None:
            QMessageBox.information(self, "Reconcile", "Select a single account first.")
            return
        if ReconcileDialog(self.ledger, self.account, self).exec():
            self.changed()

    def import_csv(self):
        if not self.need_ledger():
            return
        dlg = ImportDialog(self.ledger, self.account, self, self.settings)
        if dlg.exec():
            self.statusBar().showMessage(f"Imported {dlg.imported} transactions", 5000)
            self.changed()

    def import_clipboard(self):
        if not self.need_ledger():
            return
        dlg = ClipboardImportDialog(self.ledger, self.account, self.settings, self)
        if dlg.exec() and dlg.imported:
            self.statusBar().showMessage(f"Imported {dlg.imported} transactions", 5000)
            self.changed()

    def reports(self):
        if self.need_ledger():
            ReportDialog(self.ledger, self).exec()


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("doppia")
    win = MainWindow(sys.argv[1] if len(sys.argv) > 1 else None)
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
