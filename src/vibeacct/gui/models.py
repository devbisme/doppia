"""Qt table models over a Ledger."""

import datetime as dt
from decimal import Decimal

from PySide6.QtCore import QAbstractTableModel, QModelIndex, QSortFilterProxyModel, Qt
from PySide6.QtGui import QBrush, QColor, QFont, QPalette
from PySide6.QtWidgets import QStyledItemDelegate

NEG_COLOR = QBrush(QColor(190, 30, 30))
FUTURE_COLOR = QBrush(QColor(120, 120, 120))
POS_COLOR = QBrush(QColor(20, 150, 40))
CLEARED_COLOR = POS_COLOR
SELECTION_COLOR = QColor(200, 222, 250)  # light enough for red/green text to stay legible
CLEARED_MARK = "✔"  # display only; the CSV still stores CountAbout's "X"
SORT_HEADER_BG = QBrush(QColor(200, 220, 245))
SORT_HEADER_FG = QBrush(QColor(20, 50, 120))


class KeepColorDelegate(QStyledItemDelegate):
    """Draw selected cells in their own foreground color (e.g. red/green money) instead of the
    style's highlighted-text color, which would hide the sign of unsigned amounts."""

    def initStyleOption(self, option, index):
        super().initStyleOption(option, index)
        fg = index.data(Qt.ForegroundRole)
        if fg is not None:
            option.palette.setBrush(QPalette.HighlightedText, QBrush(fg))


def keep_colors_when_selected(view):
    """Use KeepColorDelegate and a light selection highlight so red/green text stays readable."""
    view.setItemDelegate(KeepColorDelegate(view))
    pal = view.palette()
    for group in (QPalette.Active, QPalette.Inactive):
        pal.setColor(group, QPalette.Highlight, SELECTION_COLOR)
        pal.setColor(group, QPalette.HighlightedText, pal.color(group, QPalette.Text))
    view.setPalette(pal)


def money(value, signed=False):
    """Display form of a monetary amount: $1,234.56. The sign is conveyed by money_color(),
    so it is omitted unless `signed` is set for plain-text contexts that have no color."""
    return f"{'-' if signed and value < 0 else ''}${abs(value):,.2f}"


def money_color(value):
    """Red for negative, green for positive, None (default color) for zero."""
    if value < 0:
        return NEG_COLOR
    if value > 0:
        return POS_COLOR
    return None


def money_html(value):
    """money() wrapped in a colored span, for rich-text labels."""
    brush = money_color(value)
    text = money(value)
    return f'<span style="color:{brush.color().name()}">{text}</span>' if brush else text


class RegisterModel(QAbstractTableModel):
    """Rows of one account (or all accounts), newest first, with running balance."""

    COLS = ["Date", "Account", "Ref", "Description", "Category", "Memo", "Amount", "Clr", "Balance"]
    C_DATE, C_ACCOUNT, C_REF, C_DESC, C_CAT, C_MEMO, C_AMOUNT, C_CLR, C_BAL = range(len(COLS))

    def __init__(self, ledger, account=None):
        super().__init__()
        self.ledger = ledger
        self.account = account
        self.rows = []
        self.balances = []
        self.sort_column, self.sort_order = self.C_DATE, Qt.DescendingOrder
        self.refresh()

    def set_ledger(self, ledger):
        self.ledger = ledger
        self.refresh()

    def set_account(self, account):
        self.account = account
        self.refresh()

    def refresh(self):
        self.beginResetModel()
        self.today = dt.date.today()
        if self.ledger is None:
            rows = []
        elif self.account is None:
            rows = list(self.ledger.transactions)
        else:
            rows = self.ledger.for_account(self.account)
        # Ledger order is newest first; within a date the file lists later entries first.
        # Running balance accumulates in chronological order, i.e. reversed list order.
        rows.sort(key=lambda t: t.date, reverse=True)  # stable: preserves within-date order
        bal = Decimal("0")
        balances = []
        for t in reversed(rows):
            bal += t.amount
            balances.append(bal)
        self.rows, self.balances = rows, balances[::-1]
        self._sort_rows()
        self.endResetModel()

    def _sort_key(self, column):
        """Key function on row number. Computed once per row per sort, which matters at 10k+ rows:
        letting QSortFilterProxyModel sort would call data() on every comparison."""
        rows, bals = self.rows, self.balances
        if column == self.C_DATE:
            return lambda i: rows[i].date
        if column == self.C_AMOUNT:
            return lambda i: rows[i].amount
        if column == self.C_BAL:
            return lambda i: bals[i]
        if column == self.C_CLR:
            return lambda i: rows[i].is_cleared
        attr = {
            self.C_ACCOUNT: "account",
            self.C_REF: "ref",
            self.C_DESC: "description",
            self.C_CAT: "category",
            self.C_MEMO: "memo",
        }[column]
        return lambda i: getattr(rows[i], attr).lower()

    def _sort_rows(self):
        # Stable in both directions, so ties keep the previous order (e.g. newest-first within a date).
        order = sorted(
            range(len(self.rows)), key=self._sort_key(self.sort_column), reverse=self.sort_order == Qt.DescendingOrder
        )
        self.rows = [self.rows[i] for i in order]
        self.balances = [self.balances[i] for i in order]

    def sort(self, column, order=Qt.AscendingOrder):
        """Sort in place, keeping selections and the current row attached to their transactions."""
        self.layoutAboutToBeChanged.emit()
        old = self.persistentIndexList()
        txns = [self.rows[i.row()] for i in old]
        self.sort_column, self.sort_order = column, order
        self._sort_rows()
        pos = {id(t): r for r, t in enumerate(self.rows)}
        self.changePersistentIndexList(old, [self.index(pos[id(t)], i.column()) for t, i in zip(txns, old)])
        self.layoutChanged.emit()

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.COLS)

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if orientation == Qt.Horizontal and role == Qt.DisplayRole:
            return self.COLS[section]
        return None

    def txn(self, row):
        return self.rows[row]

    def txn_changed(self, txn):
        """Repaint one transaction's row without resetting the model."""
        for r, t in enumerate(self.rows):
            if t is txn:
                self.dataChanged.emit(self.index(r, 0), self.index(r, len(self.COLS) - 1))
                return

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        t = self.rows[index.row()]
        c = index.column()
        if role == Qt.DisplayRole:
            return {
                self.C_DATE: t.date_str,
                self.C_ACCOUNT: t.account,
                self.C_REF: t.ref,
                self.C_DESC: t.description,
                self.C_CAT: t.category,
                self.C_MEMO: t.memo,
                self.C_AMOUNT: money(t.amount),
                self.C_CLR: CLEARED_MARK if t.is_cleared else "",
                self.C_BAL: money(self.balances[index.row()]),
            }[c]
        if role == Qt.ForegroundRole:
            if c == self.C_AMOUNT and t.amount:
                return money_color(t.amount)
            if c == self.C_BAL and self.balances[index.row()]:
                return money_color(self.balances[index.row()])
            if c == self.C_CLR:
                return CLEARED_COLOR
            if t.date > self.today:
                return FUTURE_COLOR
        if role == Qt.TextAlignmentRole and c in (self.C_AMOUNT, self.C_BAL):
            return int(Qt.AlignRight | Qt.AlignVCenter)
        if role == Qt.TextAlignmentRole and c == self.C_CLR:
            return int(Qt.AlignCenter)
        if role == Qt.ToolTipRole and t.is_split:
            return "Split transaction (CountAbout export does not include split details)"
        return None


class RegisterFilter(QSortFilterProxyModel):
    """Case-insensitive text filter across description, category, memo, ref, and amount.
    Sorting is done by RegisterModel; this proxy only filters."""

    def __init__(self):
        super().__init__()
        self.text = ""
        self.uncleared_only = False

    def sort(self, column, order=Qt.AscendingOrder):
        """Delegate sorting to the source model (much faster than the proxy's comparison sort)."""
        self.sourceModel().sort(column, order)
        self.headerDataChanged.emit(Qt.Horizontal, 0, self.columnCount() - 1)

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        """Highlight the header of the column currently used for sorting."""
        if orientation == Qt.Horizontal and section == self.sourceModel().sort_column:
            if role == Qt.FontRole:
                f = QFont()
                f.setBold(True)
                return f
            if role == Qt.BackgroundRole:
                return SORT_HEADER_BG
            if role == Qt.ForegroundRole:
                return SORT_HEADER_FG
        return super().headerData(section, orientation, role)

    def set_text(self, text):
        self.text = text.lower().strip()
        self.invalidateFilter()

    def set_uncleared_only(self, on):
        self.uncleared_only = on
        self.invalidateFilter()

    def filterAcceptsRow(self, row, parent):
        t = self.sourceModel().txn(row)
        if self.uncleared_only and t.is_cleared:
            return False
        if not self.text:
            return True
        hay = " ".join((t.description, t.category, t.memo, t.ref, t.amount_str, money(t.amount), t.date_str)).lower()
        return all(word in hay for word in self.text.split())
