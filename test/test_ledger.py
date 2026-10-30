import datetime as dt
import os
from decimal import Decimal

import pytest

from doppia.importer import Profile, apply_candidates, build_candidates, parse_money
from doppia.ledger import COLUMNS, Ledger, Transaction

HEADER = 'Account,Date,Description,Category,Split,"Split ID",Amount,Ref,Memo,Tags,Cleared\n'
SAMPLE = HEADER + (
    'Checking,11-16-2026,"Power Co","Gas & Electric",N,NA,-150.00,,,,\n'
    "Mastercard,10-24-2026,Grocer,Groceries,N,NA,-12.34,,,,X\n"
    "Visa,10-19-2026,transfer,{Checking},N,NA,25.00,,,,\n"
    "Checking,10-19-2026,transfer,{Visa},N,NA,-25.00,,,,X\n"
    'Checking,10-16-2026,Employer,Salary,N,NA,3000.00,,"say ""hi"", ok",,X\n'
)
LEDGER1 = os.path.join(os.path.dirname(__file__), "ledger1.csv")  # large anonymized ledger


@pytest.fixture
def ledger(tmp_path):
    p = tmp_path / "data.csv"
    p.write_text(SAMPLE)
    return Ledger.load(str(p))


def test_roundtrip_sample(ledger):
    assert ledger.to_text() == SAMPLE


def test_roundtrip_ledger1():
    with open(LEDGER1, newline="") as f:
        assert Ledger.load(LEDGER1).to_text() == f.read()


def test_save_makes_backup(ledger):
    ledger.transactions[1].memo = "edited"
    ledger.save()
    assert os.path.exists(ledger.path + ".bak")
    assert "edited" in open(ledger.path).read()


def test_balances(ledger):
    assert ledger.balance("Checking") == Decimal("2825.00")
    assert ledger.balance("Checking", cleared_only=True) == Decimal("2975.00")
    assert ledger.balance("Checking", as_of=dt.date(2026, 10, 26)) == Decimal("2975.00")


def test_transfer_partner_and_update(ledger):
    ck = ledger.transactions[3]
    partner = ledger.find_transfer_partner(ck)
    assert partner.account == "Visa"
    new = ck.copy()
    new.amount = Decimal("-30")
    new.date = dt.date(2026, 10, 20)
    ledger.update(ck, new)
    mirror = ledger.find_transfer_partner(ck)
    assert mirror.amount == Decimal("30.00") and mirror.date_str == "10-20-2026"
    assert len(ledger.transactions) == 5


def test_add_transfer_creates_mirror_and_keeps_order(ledger):
    t = Transaction(account="Mastercard", description="pay", category="{Checking}")
    t.date = dt.date(2026, 10, 25)
    t.amount = Decimal("100")
    ledger.add(t)
    assert len(ledger.transactions) == 7
    dates = [x.date for x in ledger.transactions]
    assert dates == sorted(dates, reverse=True)
    ledger.delete(t)
    assert len(ledger.transactions) == 5


def test_create_and_rename_account(ledger):
    ledger.create_account("Visa-9999", Decimal("-5"))
    assert "Visa-9999" in ledger.accounts()
    ledger.rename_account("Visa", "Card")
    assert any(t.category == "{Card}" for t in ledger.transactions)
    with pytest.raises(ValueError):
        ledger.create_account("Card")


def test_parse_money():
    assert parse_money("$1,234.50") == Decimal("1234.50")
    assert parse_money("(12.00)") == Decimal("-12.00")
    assert parse_money("12.00-") == Decimal("-12.00")


def test_import_categorizes_and_dedups(ledger):
    rows = [
        {"Date": "10/23/2026", "Desc": "GROCER #1234 SPRINGFIELD", "Amt": "12.34"},
        {"Date": "10/25/2026", "Desc": "GROCER #1234 SPRINGFIELD", "Amt": "20.00"},
        {"Date": "bad", "Desc": "X", "Amt": "1"},
    ]
    prof = Profile(date_col="Date", description_col="Desc", amount_col="Amt", negate=True)
    cands = build_candidates(ledger, "Mastercard", rows, prof)
    assert cands[0].duplicate_of is ledger.transactions[1] and not cands[0].include
    assert cands[1].include and cands[1].txn.category == "Groceries" and cands[1].txn.description == "Grocer"
    assert cands[1].txn.amount == Decimal("-20.00")
    assert cands[2].error
    assert apply_candidates(ledger, cands) == 1


def test_account_balances_matches_balance(ledger):
    today = dt.date(2026, 10, 26)
    for acct, (bal, clr) in ledger.account_balances(as_of=today).items():
        assert bal == ledger.balance(acct, as_of=today)
        assert clr == ledger.balance(acct, cleared_only=True)


def test_report_export_formats():
    from doppia import export

    headers = ["Category", "Total", "Count"]
    rows = [["Food | Dining", Decimal("-1234.5"), 3], ["— Net", Decimal("10"), ""]]
    assert export.to_csv(headers, rows) == "Category,Total,Count\nFood | Dining,-1234.50,3\n— Net,10.00,\n"
    md = export.to_markdown("Report", ["Dates: x"], headers, rows)
    assert "|---|---:|---|" in md
    assert "| Food \\| Dining | -$1,234.50 | 3 |" in md
    assert export.to_text("Report", ["Dates: x"], headers, rows).splitlines()[5:] == [
        "Category            Total  Count",
        "-------------  ----------  -----",
        "Food | Dining  -$1,234.50      3",
        "— Net              $10.00",
    ]
    page = export.to_html("Report", [], headers, rows, total_prefix="— ")
    assert "-$1,234.50" in page and 'style="font-weight:bold"' in page


def test_search_and_replace():
    import re

    led = Ledger(
        [
            Transaction("Checking", "01-02-2026", "AMZN Mktp US", "Shopping", memo="amzn order"),
            Transaction("Card", "01-03-2026", "Amzn Prime", "Subscriptions"),
            Transaction("Checking", "01-04-2026", "Pay card", "{Card}", amount_str="-10.00"),
        ]
    )
    pat = re.compile(r"amzn\b", re.IGNORECASE)
    found = led.search(pat, ["description", "memo"])
    assert [(t.description, attr, new) for t, attr, _, new in found] == [
        ("AMZN Mktp US", "description", None),
        ("AMZN Mktp US", "memo", None),
        ("Amzn Prime", "description", None),
    ]
    assert len(led.search(pat, ["description"], accounts={"Card"})) == 1
    found = led.search(pat, ["description"], replacement="Amazon")
    assert [new for *_, new in found] == ["Amazon Mktp US", "Amazon Prime"]
    led.apply_replacements([(t, attr, new) for t, attr, _, new in found])
    assert led.dirty and led.transactions[1].description == "Amazon Prime"

    assert Ledger.replacement_problem("category", "{Card}", "{Visa}")
    assert Ledger.replacement_problem("category", "Shopping", "{Card}")
    assert Ledger.replacement_problem("memo", "a", "a")
    assert Ledger.replacement_problem("memo", "a", "a\nb")
    assert Ledger.replacement_problem("category", "Shopping", "Retail") is None


def test_llm_rows_and_checks(monkeypatch):
    import json

    from doppia import llm

    reply = {
        "done_reason": "stop",
        "message": {
            "content": json.dumps(
                {
                    "transactions": [
                        {"date": "2026-10-24", "description": " INGLES #42 ", "amount": -87.65, "ref": "", "pending": False},
                        {"date": "2026-10-25", "description": "SHELL", "amount": -45.1, "ref": "", "pending": True},
                    ]
                }
            )
        },
    }
    monkeypatch.setattr(llm, "_request", lambda path, body=None, timeout=10: reply)
    rows = llm.extract_transactions("text", "Checking", dt.date(2026, 10, 27))
    assert rows == [
        {"date": "2026-10-24", "description": "INGLES #42", "amount": "-87.65", "ref": "", "pending": ""},
        {"date": "2026-10-25", "description": "SHELL", "amount": "-45.10", "pending": "1", "ref": ""},
    ]
    # Rows feed the CSV importer's pipeline; pending rows aren't marked cleared.
    led = Ledger([Transaction("Checking", "10-23-2026", "Ingles", "Groceries", amount_str="-87.65")])
    cands = build_candidates(led, "Checking", rows, llm.LLM_PROFILE)
    assert cands[0].duplicate_of is led.transactions[0] and not cands[0].include
    cands[1].pending = True
    assert apply_candidates(led, cands) == 1
    assert led.transactions[0].description == "SHELL" and not led.transactions[0].is_cleared

    reply["done_reason"] = "length"
    with pytest.raises(llm.LLMError, match="cut off"):
        llm.extract_transactions("text", "Checking", dt.date(2026, 10, 27))
    reply.update(done_reason="stop", message={"content": ""})
    with pytest.raises(llm.LLMError, match="usable JSON"):
        llm.extract_transactions("text", "Checking", dt.date(2026, 10, 27))

    text = "Oct 9 PAYMENT -1,250.00\nOct 10 FEE $112.50 10/05\nDEP $3,000"
    assert llm.amount_in_text(Decimal("1250.00"), text)
    assert llm.amount_in_text(Decimal("-112.50"), text)
    assert llm.amount_in_text(Decimal("3000.00"), text)
    assert not llm.amount_in_text(Decimal("12.50"), text)  # only inside 112.50
    assert not llm.amount_in_text(Decimal("125.00"), text)


def test_llm_unreachable(monkeypatch):
    from doppia import llm

    monkeypatch.setenv("OLLAMA_HOST", "127.0.0.1:9")  # discard port: nothing listens
    with pytest.raises(llm.LLMError, match="Could not reach Ollama"):
        llm.list_models()


def test_bank_csv_trailing_comma(tmp_path):
    from doppia.importer import read_bank_csv

    # Some banks end each data row, but not the header, with an extra comma.
    path = tmp_path / "bank.csv"
    path.write_text(
        "Account: CHECKING\n"
        '"Details","Posting Date","Description","Amount"\n'
        '"DEBIT","10/24/2026","INGLES","-87.65",\n'
        '"CREDIT","10/23/2026","Employer","3000.00",\n'
    )
    header, rows = read_bank_csv(path)
    assert header == ["Details", "Posting Date", "Description", "Amount"]
    assert [r["Amount"] for r in rows] == ["-87.65", "3000.00"]


def test_llm_guess_profile_checks(monkeypatch):
    from doppia import llm

    header = ["Date", "Desc", "Amt"]
    rows = [{"Date": "24 Oct 2026", "Desc": "SHELL", "Amt": "45.12"}, {"Date": "22 Oct 2026", "Desc": "PAY", "Amt": "-500"}]
    rows.append({"Date": "20 Oct 2026", "Desc": "SPOTIFY", "Amt": "11.99"})
    answer = dict(date_col="Date", date_format="%m/%d/%Y", description_col="Desc", amount_col="",
                  debit_col="Amt", credit_col="Amt", memo_col="", ref_col="")
    monkeypatch.setattr(llm, "_chat_json", lambda *a, **k: dict(answer))
    p, problems = llm.guess_profile(header, rows, "Card")
    assert problems == []
    assert (p.amount_col, p.debit_col, p.credit_col) == ("Amt", "", "")  # same column for both -> signed amount
    assert p.date_format == ""  # wrong format replaced by auto-detect, which handles "24 Oct 2026"
    assert p.negate  # mostly positive amounts: charges listed as positive

    answer.update(date_col="Desc")
    p, problems = llm.guess_profile(header, rows, "Card")
    assert problems and "date column" in problems[0]


def test_delete_rows_with_transfer_partners(ledger):
    power, _, visa, checking, salary = ledger.transactions
    # Both sides of a transfer selected: each is deleted once.
    rows = ledger.with_transfer_partners([power, visa, checking])
    assert len(rows) == 3
    ledger.delete_rows(rows)
    assert ledger.transactions[1:] == [salary] and ledger.dirty

    # Identical same-day transfers: each keeps its own mirror.
    led = Ledger()
    for _ in range(2):
        t = Transaction("Checking", "10-19-2026", "x", "{Visa}", amount_str="-25.00")
        led.add(t)
    a, b = [t for t in led.transactions if t.account == "Checking"]
    rows = led.with_transfer_partners([a, b])
    assert len(rows) == 4
    led.delete_rows(rows)
    assert led.transactions == []


def test_fit_columns():
    from doppia.gui.models import fit_widths, untruncated_width

    # Fewer than 5% may be cut off: none of 20 entries (1 would be exactly 5%), 1 of 21, 2 of 41.
    assert untruncated_width(list(range(1, 21))) == 20
    assert untruncated_width(list(range(1, 22))) == 20
    assert untruncated_width(list(range(1, 42))) == 39
    assert untruncated_width([]) == 0

    widths = {"memo": 100, "desc": 80, "cat": 60, "date": 50}
    floors = {"memo": 40, "desc": 50, "cat": 55, "date": 50}
    order = ["memo", "desc", "cat"]
    assert fit_widths(widths, floors, order, 500) == widths  # already fits
    assert fit_widths(widths, floors, order, 270) == {**widths, "memo": 80}  # only memo needed
    assert fit_widths(widths, floors, order, 200) == {**widths, "memo": 40, "desc": 50}
    assert fit_widths(widths, floors, order, 10) == {**floors}  # can't fit: stops at the floors


def test_even_widths():
    from doppia.gui.models import even_widths

    mins = {"a": 10, "b": 10, "c": 10}
    assert even_widths({"a": 50, "b": 50, "c": 50}, mins, 180) == {"a": 60, "b": 60, "c": 60}  # grow
    assert even_widths({"a": 50, "b": 60, "c": 70}, mins, 150) == {"a": 40, "b": 50, "c": 60}  # shrink
    assert sum(even_widths({"a": 50, "b": 50, "c": 50}, mins, 151).values()) == 151  # leftover pixel
    # A column at its minimum drops out and the others share the rest.
    assert even_widths({"a": 15, "b": 50, "c": 50}, mins, 95) == {"a": 10, "b": 42, "c": 43}
    assert even_widths({"a": 50, "b": 50, "c": 50}, mins, 5) == mins  # minimums don't fit


def split_rows(ledger, sid="abc123"):
    """A $100 purchase split 60/40."""
    return [
        Transaction("Checking", "10-20-2026", "Costco", "Groceries", "Y", sid, "-60.00", "7", "food"),
        Transaction("Checking", "10-20-2026", "Costco", "Household", "Y", sid, "-40.00", "7", ""),
    ]


def test_split_add_keeps_lines_adjacent_and_sums(ledger):
    before = ledger.balance("Checking")
    lines = split_rows(ledger)
    ledger.add_split(lines)
    i = ledger.transactions.index(lines[0])
    assert ledger.transactions[i + 1] is lines[1]
    assert ledger.balance("Checking") == before - 100


def test_split_roundtrip(ledger, tmp_path):
    ledger.add_split(split_rows(ledger))
    path = str(tmp_path / "out.csv")
    ledger.save(path)
    again = Ledger.load(path)
    assert again.to_text() == ledger.to_text()
    summary = next(t for t in again.entries() if t.lines)
    assert [t.category for t in summary.lines] == ["Groceries", "Household"]


def test_entries_collapses_splits_only(ledger):
    lines = split_rows(ledger)
    ledger.add_split(lines)
    entries = ledger.entries()
    assert len(entries) == len(ledger.transactions) - 1
    summary = next(t for t in entries if t.lines)
    assert summary.lines == lines
    assert (summary.category, summary.amount, summary.memo) == ("{SPLIT}", Decimal("-100.00"), "")
    assert not summary.is_old_split
    assert summary.transfer_account is None


def test_old_split_row_is_unchanged(tmp_path):
    text = HEADER + "Checking,10-20-2026,Costco,{SPLIT},Y,NA,-100.00,,,,X\n"
    p = tmp_path / "old.csv"
    p.write_text(text)
    led = Ledger.load(str(p))
    t = led.transactions[0]
    assert t.is_old_split and t.split_group is None and led.entries() == [t]
    assert led.to_text() == led.to_countabout_text() == text


def test_ledger1_countabout_export(tmp_path):
    led = Ledger.load(LEDGER1)
    p = tmp_path / "export.csv"
    p.write_text(led.to_countabout_text())
    exported = Ledger.load(str(p))
    assert len(exported.transactions) == len(led.entries())
    assert not any(t.split_group for t in exported.transactions)
    assert sum(t.amount for t in exported.transactions) == sum(t.amount for t in led.transactions)
    assert next(t for t in exported.transactions if t.description == "Payee 0798" and t.is_split).memo == "Memo 1489; 2345"


def test_split_cleared_and_delete_act_on_the_group(ledger):
    lines = split_rows(ledger)
    ledger.add_split(lines)
    n = len(ledger.transactions)
    summary = next(t for t in ledger.entries() if t.lines)
    ledger.set_cleared(summary, True)
    assert summary.is_cleared and all(t.is_cleared for t in lines)
    ledger.set_cleared(lines[1], False)
    assert not any(t.is_cleared for t in lines)
    ledger.delete(summary)
    assert len(ledger.transactions) == n - 2


def test_delete_rows_with_split_lines(ledger):
    lines = split_rows(ledger)
    ledger.add_split(lines)
    n = len(ledger.transactions)
    summary = next(t for t in ledger.entries() if t.lines)
    ledger.delete_rows(ledger.with_transfer_partners(ledger.with_split_lines([summary, lines[0]])))
    assert len(ledger.transactions) == n - 2


def test_replace_shared_field_updates_whole_split(ledger):
    lines = split_rows(ledger)
    ledger.add_split(lines)
    ledger.apply_replacements([(lines[0], "description", "Sam's"), (lines[1], "memo", "soap")])
    assert [t.description for t in lines] == ["Sam's", "Sam's"]
    assert [t.memo for t in lines] == ["food", "soap"]


def test_update_split_keeps_place_and_handles_date_change(ledger):
    lines = split_rows(ledger)
    ledger.add_split(lines)
    at = ledger.transactions.index(lines[0])
    summary = next(t for t in ledger.entries() if t.lines)
    new = [t.copy() for t in lines] + [lines[1].copy()]
    new[1].amount, new[2].amount = Decimal("-30"), Decimal("-10")
    ledger.update_split(summary, new)
    assert ledger.transactions[at : at + 3] == new
    summary = next(t for t in ledger.entries() if t.lines)
    moved = [t.copy() for t in new]
    for t in moved:
        t.date = dt.date(2026, 11, 20)
    ledger.update_split(summary, moved)
    assert ledger.transactions[:3] == moved


def test_plain_to_split_and_back(ledger):
    power = ledger.transactions[0]
    n = len(ledger.transactions)
    a = Transaction("Checking", power.date_str, "Power Co", "Gas", "Y", "s1", "-100.00")
    b = Transaction("Checking", power.date_str, "Power Co", "Electric", "Y", "s1", "-50.00")
    ledger.update_split(power, [a, b])
    assert ledger.transactions[:2] == [a, b] and len(ledger.transactions) == n + 1
    summary = ledger.entries()[0]
    plain = Transaction("Checking", power.date_str, "Power Co", "Utilities", amount_str="-150.00")
    ledger.update_split(summary, [plain])
    assert ledger.transactions[0] is plain and len(ledger.transactions) == n


def test_transfer_to_split_removes_mirror_and_back_restores_it(ledger):
    ck = ledger.transactions[3]
    n = len(ledger.transactions)
    a = Transaction("Checking", ck.date_str, "transfer", "Fees", "Y", "s1", "-20.00")
    b = Transaction("Checking", ck.date_str, "transfer", "Misc", "Y", "s1", "-5.00")
    ledger.update_split(ck, [a, b])
    assert len(ledger.transactions) == n  # one mirror gone, one line more
    assert not any(t.account == "Visa" and t.category == "{Checking}" for t in ledger.transactions)
    back = Transaction("Checking", ck.date_str, "transfer", "{Visa}", amount_str="-25.00")
    ledger.update_split(ledger.entries()[3], [back])
    assert ledger.find_transfer_partner(back).amount == Decimal("25.00")


def test_duplicate_detection_matches_split_total(ledger):
    ledger.add_split(split_rows(ledger))
    profile = Profile(date_col="d", description_col="x", amount_col="a")
    cands = build_candidates(ledger, "Checking", [{"d": "10-20-2026", "x": "COSTCO", "a": "-100.00"}], profile)
    assert cands[0].duplicate_of is not None and not cands[0].include
    apply_candidates(ledger, cands)
    assert all(t.is_cleared for t in cands[0].duplicate_of.lines)


def test_countabout_export_collapses_splits(ledger):
    ledger.add_split(split_rows(ledger))
    text = ledger.to_countabout_text()
    assert "Checking,10-20-2026,Costco,{SPLIT},Y,NA,-100.00,7,food,," in text
    assert "abc123" not in text and "Household" not in text
    assert "abc123" in ledger.to_text()  # the ledger itself is unchanged


def test_new_ledger_builds_and_roundtrips(tmp_path):
    led = Ledger()
    assert led.accounts() == [] and led.entries() == [] and not led.dirty
    assert led.to_text() == HEADER
    led.create_account("Checking", Decimal("100"))
    led.add(Transaction("Checking", "10-20-2026", "Grocer", "Groceries", amount_str="-12.34"))
    assert led.dirty
    path = str(tmp_path / "new.csv")
    led.save(path)
    assert Ledger.load(path).to_text() == led.to_text()
    assert Ledger.load(path).balance("Checking") == Decimal("87.66")


def test_header_only_file_loads(tmp_path):
    p = tmp_path / "empty.csv"
    p.write_text(HEADER)
    assert Ledger.load(str(p)).transactions == []
