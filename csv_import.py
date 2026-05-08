"""CSV bank statement parser.

Tolerant to common bank export formats. Looks for date, description, and amount.
If amount is split into Debit/Credit columns, combines them (Debit = negative).
"""
import io
import pandas as pd
from datetime import datetime


DATE_CANDIDATES = ["date", "transaction date", "posted date", "post date", "trans date"]
DESC_CANDIDATES = ["description", "memo", "details", "narration", "transaction", "payee", "merchant"]
AMOUNT_CANDIDATES = ["amount", "transaction amount", "value"]
DEBIT_CANDIDATES = ["debit", "withdrawal", "withdrawals", "money out", "outflow"]
CREDIT_CANDIDATES = ["credit", "deposit", "deposits", "money in", "inflow"]


def _find_column(df_cols_lower, candidates):
    for c in candidates:
        if c in df_cols_lower:
            return df_cols_lower[c]
    return None


def _parse_date(value):
    if pd.isna(value):
        return None
    if isinstance(value, (datetime, pd.Timestamp)):
        return value.strftime("%Y-%m-%d")
    s = str(value).strip()
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%d/%m/%Y", "%m-%d-%Y", "%d-%m-%Y", "%Y/%m/%d", "%b %d, %Y", "%d %b %Y"):
        try:
            return datetime.strptime(s, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    # Last resort: pandas
    try:
        return pd.to_datetime(s).strftime("%Y-%m-%d")
    except Exception:
        return None


def _to_float(v):
    if pd.isna(v) or v == "":
        return 0.0
    s = str(v).replace(",", "").replace("$", "").strip()
    if s.startswith("(") and s.endswith(")"):
        s = "-" + s[1:-1]
    try:
        return float(s)
    except ValueError:
        return 0.0


def parse_bank_csv(file_storage):
    """Parse an uploaded CSV file.

    Returns a list of dicts: {date, description, amount}
    Raises ValueError on unrecoverable problems.
    """
    raw = file_storage.read()
    if not raw:
        raise ValueError("File is empty.")
    text = raw.decode("utf-8-sig", errors="replace")
    df = pd.read_csv(io.StringIO(text))
    if df.empty:
        raise ValueError("CSV has no rows.")

    cols_lower = {c.lower().strip(): c for c in df.columns}
    date_col = _find_column(cols_lower, DATE_CANDIDATES)
    desc_col = _find_column(cols_lower, DESC_CANDIDATES)
    amount_col = _find_column(cols_lower, AMOUNT_CANDIDATES)
    debit_col = _find_column(cols_lower, DEBIT_CANDIDATES)
    credit_col = _find_column(cols_lower, CREDIT_CANDIDATES)

    if date_col is None:
        raise ValueError(f"Could not find a date column. Looked for: {DATE_CANDIDATES}. Found: {list(df.columns)}")
    if desc_col is None:
        raise ValueError(f"Could not find a description column. Looked for: {DESC_CANDIDATES}. Found: {list(df.columns)}")
    if amount_col is None and (debit_col is None or credit_col is None):
        raise ValueError("Could not find an amount column or a debit/credit pair.")

    out = []
    for _, row in df.iterrows():
        date = _parse_date(row[date_col])
        desc = str(row[desc_col]).strip() if not pd.isna(row[desc_col]) else ""
        if not date or not desc:
            continue
        if amount_col is not None:
            amt = _to_float(row[amount_col])
        else:
            debit = _to_float(row[debit_col])
            credit = _to_float(row[credit_col])
            # Convention: debit (money out) is negative; credit (money in) is positive
            amt = credit - debit
        if amt == 0:
            continue
        out.append({"date": date, "description": desc, "amount": round(amt, 2)})

    if not out:
        raise ValueError("No valid transactions found in the CSV.")
    return out
