import datetime as dt
import os
from decimal import Decimal

import pytest

from pacioli.importer import Profile, apply_candidates, build_candidates, parse_money
from pacioli.ledger import COLUMNS, Ledger, Transaction

HEADER = 'Account,Date,Description,Category,Split,"Split ID",Amount,Ref,Memo,Tags,Cleared\n'
SAMPLE = HEADER + (
    'Checking,11-16-2026,"Power Co","Gas & Electric",N,NA,-150.00,,,,\n'
    "Mastercard,10-24-2026,Grocer,Groceries,N,NA,-12.34,,,,X\n"
    "Visa,10-19-2026,transfer,{Checking},N,NA,25.00,,,,\n"
    "Checking,10-19-2026,transfer,{Visa},N,NA,-25.00,,,,X\n"
    'Checking,10-16-2026,Employer,Salary,N,NA,3000.00,,"say ""hi"", ok",,X\n'
)
REAL = os.path.join(os.path.dirname(__file__), "..", "countabout-export-2026-10-26.csv")


@pytest.fixture
def ledger(tmp_path):
    p = tmp_path / "data.csv"
    p.write_text(SAMPLE)
    return Ledger.load(str(p))


def test_roundtrip_sample(ledger):
    assert ledger.to_text() == SAMPLE


@pytest.mark.skipif(not os.path.exists(REAL), reason="real export not present")
def test_roundtrip_real_export():
    with open(REAL, newline="") as f:
        assert Ledger.load(REAL).to_text() == f.read()


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
    from pacioli import export

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

    from pacioli import llm

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
    from pacioli import llm

    monkeypatch.setenv("OLLAMA_HOST", "127.0.0.1:9")  # discard port: nothing listens
    with pytest.raises(llm.LLMError, match="Could not reach Ollama"):
        llm.list_models()
