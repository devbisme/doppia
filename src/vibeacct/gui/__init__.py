"""Main window and entry point."""

import datetime as dt
import os
import sys
from decimal import Decimal

from PySide6.QtCore import QSettings, Qt
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTableView,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..ledger import Ledger
from .dialogs import ImportDialog, ReconcileDialog, ReportDialog, TransactionDialog, parse_amount
from .models import RegisterFilter, RegisterModel, keep_colors_when_selected, money, money_color

ALL_ACCOUNTS = "All accounts"


class MainWindow(QMainWindow):
    def __init__(self, path=None):
        super().__init__()
        self.settings = QSettings("vibeacct", "vibeacct")
        self.ledger = None
        self.account = None

        # Account list: name, balance as of today, cleared balance.
        self.tree = QTreeWidget()
        keep_colors_when_selected(self.tree)
        self.tree.setHeaderLabels(["Account", "Balance", "Cleared"])
        self.tree.setRootIsDecorated(False)
        self.tree.itemSelectionChanged.connect(self.account_selected)
        self.tree.header().setSectionResizeMode(0, QHeaderView.Stretch)

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
        self.view.verticalHeader().setDefaultSectionSize(22)
        self.view.clicked.connect(self.on_click)
        self.view.doubleClicked.connect(self.on_double_click)
        self.view.setContextMenuPolicy(Qt.CustomContextMenu)
        self.view.customContextMenuRequested.connect(self.context_menu)
        hdr = self.view.horizontalHeader()
        hdr.setSectionResizeMode(QHeaderView.Interactive)
        # Header clicks sort; clicking the same header again reverses the order.
        self.view.setSortingEnabled(True)
        self.view.sortByColumn(RegisterModel.C_DATE, Qt.DescendingOrder)
        for col, w in zip(range(len(RegisterModel.COLS)), (90, 130, 60, 220, 170, 240, 95, 35, 105)):
            self.view.setColumnWidth(col, w)

        self.search = QLineEdit()
        self.search.setPlaceholderText("Filter (all words must match)…")
        self.search.textChanged.connect(self.proxy.set_text)
        self.uncleared = QCheckBox("Uncleared only")
        self.uncleared.toggled.connect(self.proxy.set_uncleared_only)
        self.title = QLabel()
        self.title.setStyleSheet("font-weight: bold; font-size: 14px;")
        btns = []
        for text, slot in (("New", self.new_txn), ("Edit", self.edit_txn), ("Delete", self.delete_txn)):
            b = QPushButton(text)
            b.clicked.connect(slot)
            btns.append(b)

        bar = QHBoxLayout()
        bar.addWidget(self.title)
        bar.addStretch()
        bar.addWidget(self.search, 1)
        bar.addWidget(self.uncleared)
        for b in btns:
            bar.addWidget(b)
        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.addLayout(bar)
        rl.addWidget(self.view)

        split = QSplitter()
        split.addWidget(self.tree)
        split.addWidget(right)
        split.setSizes([330, 1100])
        self.setCentralWidget(split)
        self.build_menus()
        self.resize(1450, 850)

        path = path or self.settings.value("last_file")
        if path and os.path.exists(path):
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
        act(m, "&Open…", self.open_file, QKeySequence.Open)
        act(m, "&Save", self.save, QKeySequence.Save)
        act(m, "Save &As…", self.save_as, QKeySequence.SaveAs)
        m.addSeparator()
        act(m, "&Import bank CSV…", self.import_csv, "Ctrl+I")
        m.addSeparator()
        act(m, "&Quit", self.close, QKeySequence.Quit)

        m = self.menuBar().addMenu("&Transaction")
        act(m, "&New", self.new_txn, QKeySequence.New)
        act(m, "&Edit", self.edit_txn, "Ctrl+E")
        act(m, "&Delete", self.delete_txn, "Ctrl+Del")
        act(m, "Toggle &cleared", self.toggle_cleared, "Ctrl+K")

        m = self.menuBar().addMenu("&Account")
        act(m, "&New account…", self.new_account)
        act(m, "&Rename account…", self.rename_account)
        act(m, "Re&concile…", self.reconcile, "Ctrl+R")

        m = self.menuBar().addMenu("&Reports")
        act(m, "&Reports…", self.reports, "Ctrl+P")
        act(m, "Rename &category…", self.rename_category)

    # ------------------------------------------------------------------ file

    def open_path(self, path):
        try:
            ledger = Ledger.load(path)
        except (OSError, ValueError) as e:
            QMessageBox.critical(self, "Open", str(e))
            return
        self.ledger = ledger
        self.settings.setValue("last_file", path)
        self.model.set_ledger(ledger)
        self.account = None
        self.refresh_accounts()

    def maybe_save(self):
        if self.ledger is None or not self.ledger.dirty:
            return True
        r = QMessageBox.question(
            self, "Unsaved changes", "Save changes?", QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel
        )
        if r == QMessageBox.Save:
            return self.save()
        return r == QMessageBox.Discard

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
        self.settings.setValue("last_file", path)
        return self.save()

    def closeEvent(self, ev):
        if self.maybe_save():
            ev.accept()
        else:
            ev.ignore()

    # ------------------------------------------------------------------ view refresh

    def update_title(self):
        name = os.path.basename(self.ledger.path) if self.ledger and self.ledger.path else "(no file)"
        dirty = "*" if self.ledger and self.ledger.dirty else ""
        self.setWindowTitle(f"{dirty}{name} — vibeacct")
        self.title.setText(self.account or ALL_ACCOUNTS)

    def refresh_accounts(self, reset_register=True):
        """Rebuild the account list, keeping the current selection."""
        self.tree.blockSignals(True)
        self.tree.clear()
        today = dt.date.today()
        total = Decimal("0")
        sel = None
        for name in [ALL_ACCOUNTS] + (self.ledger.accounts() if self.ledger else []):
            if name == ALL_ACCOUNTS:
                item = QTreeWidgetItem([name, "", ""])
                f = item.font(0)
                f.setBold(True)
                item.setFont(0, f)
            else:
                bal = self.ledger.balance(name, as_of=today)
                clr = self.ledger.balance(name, cleared_only=True)
                total += bal
                item = QTreeWidgetItem([name, money(bal), money(clr)])
                for c, v in ((1, bal), (2, clr)):
                    item.setTextAlignment(c, Qt.AlignRight | Qt.AlignVCenter)
                    if v:
                        item.setForeground(c, money_color(v))
            self.tree.addTopLevelItem(item)
            if name == (self.account or ALL_ACCOUNTS):
                sel = item
        net = QTreeWidgetItem(["Net worth (today)", money(total), ""])
        net.setTextAlignment(1, Qt.AlignRight | Qt.AlignVCenter)
        if total:
            net.setForeground(1, money_color(total))
        net.setFlags(Qt.ItemIsEnabled)
        f = net.font(0)
        f.setItalic(True)
        net.setFont(0, f)
        self.tree.addTopLevelItem(net)
        self.tree.resizeColumnToContents(1)
        self.tree.resizeColumnToContents(2)
        if sel:
            self.tree.setCurrentItem(sel)
        self.tree.blockSignals(False)
        if reset_register:
            self.model.set_account(self.account)
            self.view.setColumnHidden(RegisterModel.C_ACCOUNT, self.account is not None)
        self.update_title()

    def changed(self, select_txn=None):
        """Call after any ledger mutation."""
        self.refresh_accounts()
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
        items = self.tree.selectedItems()
        if not items or self.ledger is None:
            return
        name = items[0].text(0)
        if name not in self.ledger.accounts() and name != ALL_ACCOUNTS:
            return
        self.account = None if name == ALL_ACCOUNTS else name
        self.model.set_account(self.account)
        self.view.setColumnHidden(RegisterModel.C_ACCOUNT, self.account is not None)
        self.update_title()

    def current_txn(self):
        idx = self.view.currentIndex()
        if not idx.isValid():
            return None
        return self.model.txn(self.proxy.mapToSource(idx).row())

    # ------------------------------------------------------------------ transactions

    def need_ledger(self):
        if self.ledger is None:
            QMessageBox.information(self, "vibeacct", "Open a ledger CSV first (File → Open).")
            return False
        return True

    def new_txn(self):
        if not self.need_ledger():
            return
        dlg = TransactionDialog(self.ledger, account=self.account, parent=self)
        if dlg.exec():
            t = dlg.result_txn()
            self.ledger.add(t)
            self.changed(t)

    def edit_txn(self):
        t = self.current_txn()
        if t is None:
            return
        dlg = TransactionDialog(self.ledger, txn=t, parent=self)
        if dlg.exec():
            self.ledger.update(t, dlg.result_txn())
            self.changed(t)

    def on_click(self, idx):
        if idx.column() == RegisterModel.C_CLR:
            self.toggle_cleared()

    def on_double_click(self, idx):
        if idx.column() != RegisterModel.C_CLR:
            self.edit_txn()

    def context_menu(self, pos):
        idx = self.view.indexAt(pos)
        if not idx.isValid():
            return
        self.view.setCurrentIndex(idx)
        menu = QMenu(self)
        menu.addAction("Delete transaction", self.delete_txn)
        menu.exec(self.view.viewport().mapToGlobal(pos))

    def delete_txn(self):
        t = self.current_txn()
        if t is None:
            return
        partner = self.ledger.find_transfer_partner(t)
        msg = f"Delete {t.date_str} {t.description} {money(t.amount, signed=True)}?"
        if partner is not None:
            msg += f"\n\nThe matching transfer entry in {partner.account} will also be deleted."
        if QMessageBox.question(self, "Delete", msg) == QMessageBox.Yes:
            row = self.view.currentIndex().row()
            self.ledger.delete(t)
            self.changed()
            self.view.selectRow(min(row, self.proxy.rowCount() - 1))

    def toggle_cleared(self):
        t = self.current_txn()
        if t is None:
            return
        self.ledger.set_cleared(t, not t.is_cleared)
        # Clearing doesn't change row order or running balances, so update in place
        # rather than resetting the register (which would lose the scroll position).
        self.model.txn_changed(t)
        self.refresh_accounts(reset_register=False)

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
            QMessageBox.information(self, "Reconcile", "Select an account first.")
            return
        if ReconcileDialog(self.ledger, self.account, self).exec():
            self.changed()

    def import_csv(self):
        if not self.need_ledger():
            return
        dlg = ImportDialog(self.ledger, self.account, self)
        if dlg.exec():
            self.statusBar().showMessage(f"Imported {dlg.imported} transactions", 5000)
            self.changed()

    def reports(self):
        if self.need_ledger():
            ReportDialog(self.ledger, self).exec()


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("vibeacct")
    win = MainWindow(sys.argv[1] if len(sys.argv) > 1 else None)
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
