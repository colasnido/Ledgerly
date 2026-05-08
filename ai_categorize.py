"""Google Gemini-powered transaction categorization.

Given a list of bank transactions and the chart of accounts, asks Gemini to
suggest the best account_id for each. Falls back gracefully if no API key is set.
"""
import json
import os
from google import genai
from google.genai import types

MODEL = "gemini-2.0-flash"  # free tier, fast, good for structured categorization

_client = None


def _get_client():
    global _client
    if _client is None:
        api_key = os.environ.get("GOOGLE_API_KEY")
        if not api_key or "your-google-api-key" in api_key.lower():
            return None
        _client = genai.Client(api_key=api_key)
    return _client


def categorize_transactions(transactions, accounts):
    """Return a list of dicts: [{index, account_id, reason}, ...] aligned to transactions input.

    transactions: list of {"index": int, "date": str, "description": str, "amount": float}
    accounts:     list of sqlite3.Row with id, code, name, type
    """
    client = _get_client()
    if client is None:
        return [{"index": t["index"], "account_id": None, "reason": "No API key set"} for t in transactions]

    # Build a compact account list for the prompt
    account_lines = [
        f'  {{"id": {a["id"]}, "code": "{a["code"]}", "name": "{a["name"]}", "type": "{a["type"]}"}}'
        for a in accounts
    ]
    accounts_json = "[\n" + ",\n".join(account_lines) + "\n]"

    tx_lines = [
        f'  {{"index": {t["index"]}, "date": "{t["date"]}", "description": {json.dumps(t["description"])}, "amount": {t["amount"]}}}'
        for t in transactions
    ]
    tx_json = "[\n" + ",\n".join(tx_lines) + "\n]"

    system_prompt = (
        "You are a bookkeeping assistant. You categorize bank transactions to a chart of accounts.\n"
        "Rules:\n"
        "- Positive amounts are money INTO the bank account (deposits, income, refunds).\n"
        "- Negative amounts are money OUT (expenses, payments).\n"
        "- For positive amounts, suggest an Income account (or sometimes Asset/Liability).\n"
        "- For negative amounts, suggest an Expense account (or sometimes Asset/Liability).\n"
        "- Use the merchant name and memo to infer the category.\n"
        "- If genuinely unclear, suggest 'Miscellaneous Expense' for outflows or 'Other Income' for inflows.\n"
        "- Reply ONLY with a JSON array. Each item: {\"index\": N, \"account_id\": N, \"reason\": \"short phrase\"}"
    )

    user_prompt = (
        f"CHART OF ACCOUNTS:\n{accounts_json}\n\n"
        f"TRANSACTIONS TO CATEGORIZE:\n{tx_json}\n\n"
        "Return the JSON array now."
    )

    try:
        resp = client.models.generate_content(
            model=MODEL,
            contents=user_prompt,
            config=types.GenerateContentConfig(
                system_instruction=system_prompt,
                response_mime_type="application/json",
                max_output_tokens=4096,
                temperature=0.2,
            ),
        )
        text = (resp.text or "").strip()
        # response_mime_type=json should give pure JSON; strip code fences defensively just in case
        if text.startswith("```"):
            text = text.split("```", 2)[1]
            if text.startswith("json"):
                text = text[4:]
            text = text.strip().rstrip("`").strip()
        suggestions = json.loads(text)
        # Validate shape
        valid_ids = {a["id"] for a in accounts}
        out = []
        for s in suggestions:
            aid = s.get("account_id")
            if aid not in valid_ids:
                aid = None
            out.append({
                "index": s.get("index"),
                "account_id": aid,
                "reason": s.get("reason", "")[:200],
            })
        return out
    except Exception as e:
        return [{"index": t["index"], "account_id": None, "reason": f"AI error: {e}"} for t in transactions]
