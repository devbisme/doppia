"""Extract transactions from free-form text (e.g. copied from a bank website) with a local
LLM served by Ollama (https://ollama.com). Uses only the standard library.

Results are rows of strings shaped for importer.build_candidates() with LLM_PROFILE, so
pasted transactions get the same payee matching and duplicate detection as CSV imports.
"""

import json
import os
import re
import urllib.error
import urllib.request
from decimal import Decimal, InvalidOperation

from .importer import Profile

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
    body = {
        "model": model,
        "stream": False,
        "think": False,
        "format": SCHEMA,
        "options": {"temperature": 0, "num_ctx": CONTEXT_TOKENS},
        "messages": [{"role": "user", "content": PROMPT.format(account=account, today=today.isoformat(), text=text)}],
    }
    reply = _request("/api/chat", body, timeout=TIMEOUT_SECONDS)
    if reply.get("done_reason") == "length":
        raise LLMError("The model's reply was cut off. Copy fewer transactions at a time.")
    content = reply.get("message", {}).get("content", "")
    try:
        found = json.loads(content)["transactions"]
    except (ValueError, KeyError, TypeError):
        raise LLMError(f"{model} did not return usable JSON; try another model. Reply began: {content[:200]!r}")
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
