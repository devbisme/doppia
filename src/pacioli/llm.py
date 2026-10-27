"""Extract transactions from free-form text (e.g. copied from a bank website) with a local
LLM served by Ollama (https://ollama.com). Uses only the standard library.

Results are rows of strings shaped for importer.build_candidates() with LLM_PROFILE, so
pasted transactions get the same payee matching and duplicate detection as CSV imports.
"""

import csv
import io
import json
import os
import re
import urllib.error
import urllib.request
from decimal import Decimal, InvalidOperation

from .importer import DATE_FORMATS, Profile, parse_date, parse_money

DEFAULT_MODEL = "gemma4:12b-it-qat"  # the most accurate of the models tried on sample statements
CONTEXT_TOKENS = 16384  # room for a page or two of transactions plus the JSON reply
TIMEOUT_SECONDS = 600

LLM_PROFILE = Profile(
    name="local LLM",
    date_col="date",
    date_format="%Y-%m-%d",
    description_col="description",
    amount_col="amount",
    ref_col="ref",
)

# Ollama constrains the reply to this JSON schema.
SCHEMA = {
    "type": "object",
    "properties": {
        "transactions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "date": {"type": "string"},
                    "description": {"type": "string"},
                    "amount": {"type": "number"},
                    "ref": {"type": "string"},
                    "pending": {"type": "boolean"},
                },
                "required": ["date", "description", "amount", "ref", "pending"],
            },
        }
    },
    "required": ["transactions"],
}

PROMPT = """Extract every financial transaction from the text below, which was copied from a bank or \
credit card website or statement for the account "{account}". Today is {today}.

For each transaction return:
- date: the transaction date as YYYY-MM-DD. If the year is missing, use the most recent such date \
that is not after today. If there are both a transaction date and a posting date, use the posting date.
- description: the payee or description text, as written.
- amount: a number from the account holder's point of view: negative for money going out \
(purchases, withdrawals, checks, fees, transfers out, and charges on a credit card), positive for \
money coming in (deposits, interest, refunds, returns, transfers in, and payments made to a credit \
card). Credit card statements often list charges as positive and payments as negative; reverse those signs.
- ref: the check number if there is one, else "".
- pending: true if the transaction is shown as pending, else false.

Skip balances, totals, headers, and anything that is not a single transaction. Do not invent transactions.

TEXT:
{text}"""


MAPPING_PROMPT = """Below are the header and first rows of a CSV file downloaded from a bank or credit \
card website for the account "{account}". Say which column holds each field, using the exact column \
names from the header, or "" if there is no such column.

- date_col: the date to record the transaction on. If there are both a transaction date and a posting \
date, choose the posting date.
- date_format: the Python strptime format of that column's dates, chosen from the allowed list.
- description_col: the payee or description.
- amount_col: a single column holding the amount of every transaction. Leave it "" if amounts are \
split into separate debit and credit (or withdrawal and deposit) columns.
- debit_col and credit_col: two DIFFERENT columns, money out and money in, only when amounts are split \
that way. If one column holds all amounts (positive and negative), use amount_col and leave these "".
- memo_col: a memo or notes column, if any (not the description).
- ref_col: a check number or reference number column, if any.

CSV:
{sample}"""


class LLMError(Exception):
    pass


def base_url():
    """Ollama's address, honoring OLLAMA_HOST (which may omit the scheme, e.g. 0.0.0.0:11434)."""
    host = os.environ.get("OLLAMA_HOST", "").strip() or "localhost:11434"
    host = host.replace("0.0.0.0", "localhost")
    return host if "://" in host else "http://" + host


def _request(path, body=None, timeout=10):
    url = base_url() + path
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(url, data, {"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace").strip()
        raise LLMError(f"Ollama at {url} returned {e.code}: {detail}") from e
    except (urllib.error.URLError, OSError) as e:
        raise LLMError(f"Could not reach Ollama at {url} ({e}). Is it running? Start it with: ollama serve") from e
    except ValueError as e:
        raise LLMError(f"Ollama at {url} sent a reply that isn't JSON: {e}") from e


def list_models():
    """Names of installed models that can generate text (embedding-only models are left out)."""
    models = _request("/api/tags").get("models", [])
    return sorted(m["name"] for m in models if "completion" in m.get("capabilities", ["completion"]))


def extract_transactions(text, account, today, model=DEFAULT_MODEL):
    """Rows of strings {date, description, amount, ref, pending} found in `text` by the model.
    `pending` is "1" or ""."""
    got = _chat_json(PROMPT.format(account=account, today=today.isoformat(), text=text), SCHEMA, model)
    try:
        found = got["transactions"]
    except (KeyError, TypeError):
        raise LLMError(f"{model} did not return a list of transactions; try another model.")
    rows = []
    for t in found:
        try:
            amount = Decimal(str(t["amount"])).quantize(Decimal("0.01"))
        except (InvalidOperation, KeyError, TypeError):
            amount = ""  # build_candidates reports it as an error row
        rows.append(
            {
                "date": str(t.get("date", "")).strip(),
                "description": str(t.get("description", "")).strip(),
                "amount": str(amount),
                "ref": str(t.get("ref", "")).strip(),
                "pending": "1" if t.get("pending") else "",
            }
        )
    return rows


def _chat_json(prompt, schema, model, timeout=TIMEOUT_SECONDS):
    """Send one prompt and return the reply parsed as JSON matching `schema`."""
    body = {
        "model": model,
        "stream": False,
        "think": False,
        "format": schema,
        "options": {"temperature": 0, "num_ctx": CONTEXT_TOKENS},
        "messages": [{"role": "user", "content": prompt}],
    }
    reply = _request("/api/chat", body, timeout=timeout)
    if reply.get("done_reason") == "length":
        raise LLMError("The model's reply was cut off. Copy fewer transactions at a time.")
    content = reply.get("message", {}).get("content", "")
    try:
        return json.loads(content)
    except ValueError:
        raise LLMError(f"{model} did not return usable JSON; try another model. Reply began: {content[:200]!r}")


def guess_profile(header, rows, account, model=DEFAULT_MODEL, sample_size=8):
    """Ask the model to map a bank CSV's columns (header and dict rows from read_bank_csv)
    onto a Profile. Column names are constrained to the header, so it can't invent one;
    the date format and amount columns are then checked against the sample rows."""
    cols = [h for h in header if h]
    column = {"type": "string", "enum": cols + [""]}
    schema = {
        "type": "object",
        "properties": {
            "date_col": column,
            "date_format": {"type": "string", "enum": DATE_FORMATS},
            "description_col": column,
            "amount_col": column,
            "debit_col": column,
            "credit_col": column,
            "memo_col": column,
            "ref_col": column,
        },
        "required": ["date_col", "date_format", "description_col", "amount_col", "debit_col", "credit_col", "memo_col", "ref_col"],
    }
    out = io.StringIO()
    w = csv.writer(out, lineterminator="\n")
    w.writerow(header)
    sample = rows[:sample_size]
    for row in sample:
        w.writerow([row.get(h, "") for h in header])
    prompt = MAPPING_PROMPT.format(account=account, sample=out.getvalue())
    prompt += "\nAllowed date formats: " + ", ".join(DATE_FORMATS)
    got = _chat_json(prompt, schema, model, timeout=120)
    p = Profile(**{k: got.get(k, "") for k in schema["properties"]})
    if p.debit_col and p.debit_col == p.credit_col:  # one column named for both: it's a signed amount
        p.amount_col = p.debit_col
    if p.amount_col:
        p.debit_col = p.credit_col = ""
    problems = check_profile(p, sample)
    if p.amount_col and not problems:
        # Decided from the data, not the model (which got it wrong in testing): checking
        # accounts are mostly money out shown negative, so mostly-positive amounts mean a
        # card file listing charges as positive.
        amounts = [parse_money(r.get(p.amount_col, "")) for r in rows]
        amounts = [a for a in amounts if a]
        p.negate = sum(a > 0 for a in amounts) > len(amounts) / 2
    return p, problems


def check_profile(p, rows):
    """Problems with a mapping found by parsing sample rows; fixes what it safely can
    (an unparseable date format falls back to auto-detect)."""
    problems = []
    if not p.date_col or not p.description_col or not (p.amount_col or p.debit_col or p.credit_col):
        problems.append("no date, description, or amount column found")
        return problems
    dates = [r.get(p.date_col, "") for r in rows if r.get(p.date_col, "").strip()]
    try:
        for d in dates:
            parse_date(d, p.date_format)
    except ValueError:
        p.date_format = ""  # let the importer try each known format
        try:
            for d in dates:
                parse_date(d)
        except ValueError as e:
            problems.append(f"date column {p.date_col!r}: {e}")
    for col in (p.amount_col, p.debit_col, p.credit_col):
        if not col:
            continue
        try:
            for r in rows:
                parse_money(r.get(col, ""))
        except ValueError as e:
            problems.append(f"amount column {col!r}: {e}")
    return problems


def amount_in_text(amount, text):
    """Whether the amount's digits appear in the text (e.g. 1250.00 as "1,250.00", "1250" or
    "$1,250"), to catch amounts the model misread or made up. Signs aren't checked."""
    plain = re.sub(r"[$,]", "", text)
    a = abs(Decimal(amount))
    forms = {f"{a:.2f}"}
    if a == a.to_integral_value():
        forms.add(f"{a:.0f}")
    # Require a non-digit (or the edge) on both sides so 12.50 doesn't match inside 112.50.
    return any(re.search(rf"(?<![\d.]){re.escape(f)}(?![\d])", plain) for f in forms)
