"""SQLite schema and helpers for the bookkeeping app.

Double-entry model:
- accounts: chart of accounts with type (Asset/Liability/Equity/Income/Expense)
- transactions: a logical event (header)
- ledger_entries: one or more debit/credit lines per transaction; sum(debit)=sum(credit)
- bank_imports: raw CSV rows staged before being posted to a transaction
"""
import sqlite3
from contextlib import contextmanager
from pathlib import Path

DB_PATH = Path(__file__).parent / "bookkeeping.db"


SCHEMA = """
CREATE TABLE IF NOT EXISTS accounts (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    code            TEXT UNIQUE NOT NULL,
    name            TEXT NOT NULL,
    type            TEXT NOT NULL CHECK(type IN ('Asset','Liability','Equity','Income','Expense')),
    normal_balance  TEXT NOT NULL CHECK(normal_balance IN ('debit','credit')),
    active          INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS transactions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    date        TEXT NOT NULL,
    description TEXT NOT NULL,
    source      TEXT NOT NULL DEFAULT 'manual',
    reconciled  INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS ledger_entries (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    transaction_id  INTEGER NOT NULL REFERENCES transactions(id) ON DELETE CASCADE,
    account_id      INTEGER NOT NULL REFERENCES accounts(id),
    debit           REAL NOT NULL DEFAULT 0,
    credit          REAL NOT NULL DEFAULT 0,
    memo            TEXT
);

CREATE TABLE IF NOT EXISTS bank_imports (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_id              TEXT NOT NULL,
    date                  TEXT NOT NULL,
    description           TEXT NOT NULL,
    amount                REAL NOT NULL,
    suggested_account_id  INTEGER REFERENCES accounts(id),
    suggestion_reason     TEXT,
    posted                INTEGER NOT NULL DEFAULT 0,
    transaction_id        INTEGER REFERENCES transactions(id),
    created_at            TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_ledger_account ON ledger_entries(account_id);
CREATE INDEX IF NOT EXISTS idx_ledger_tx ON ledger_entries(transaction_id);
CREATE INDEX IF NOT EXISTS idx_tx_date ON transactions(date);
CREATE INDEX IF NOT EXISTS idx_imports_batch ON bank_imports(batch_id);
"""


def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


@contextmanager
def db():
    conn = get_conn()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db():
    """Create tables and seed the default chart of accounts if empty."""
    with db() as conn:
        conn.executescript(SCHEMA)
        existing = conn.execute("SELECT COUNT(*) AS n FROM accounts").fetchone()["n"]
        if existing == 0:
            seed_chart_of_accounts(conn)


def seed_chart_of_accounts(conn):
    """A minimal but realistic small-business chart of accounts."""
    accounts = [
        # Assets (1000s) — normal balance: debit
        ("1000", "Cash - Operating", "Asset", "debit"),
        ("1010", "Cash - Savings", "Asset", "debit"),
        ("1100", "Accounts Receivable", "Asset", "debit"),
        ("1200", "Inventory", "Asset", "debit"),
        ("1500", "Equipment", "Asset", "debit"),
        # Liabilities (2000s) — normal balance: credit
        ("2000", "Accounts Payable", "Liability", "credit"),
        ("2100", "Credit Card Payable", "Liability", "credit"),
        ("2200", "Sales Tax Payable", "Liability", "credit"),
        ("2500", "Loans Payable", "Liability", "credit"),
        # Equity (3000s) — normal balance: credit
        ("3000", "Owner's Equity", "Equity", "credit"),
        ("3100", "Retained Earnings", "Equity", "credit"),
        ("3200", "Owner's Draws", "Equity", "debit"),
        # Income (4000s) — normal balance: credit
        ("4000", "Sales Revenue", "Income", "credit"),
        ("4100", "Service Revenue", "Income", "credit"),
        ("4900", "Other Income", "Income", "credit"),
        # Expenses (5000s-6000s) — normal balance: debit
        ("5000", "Cost of Goods Sold", "Expense", "debit"),
        ("6000", "Rent Expense", "Expense", "debit"),
        ("6010", "Utilities Expense", "Expense", "debit"),
        ("6020", "Internet & Phone", "Expense", "debit"),
        ("6100", "Office Supplies", "Expense", "debit"),
        ("6200", "Software & Subscriptions", "Expense", "debit"),
        ("6300", "Meals & Entertainment", "Expense", "debit"),
        ("6400", "Travel", "Expense", "debit"),
        ("6500", "Advertising & Marketing", "Expense", "debit"),
        ("6600", "Professional Fees", "Expense", "debit"),
        ("6700", "Bank Fees", "Expense", "debit"),
        ("6800", "Payroll Expense", "Expense", "debit"),
        ("6900", "Miscellaneous Expense", "Expense", "debit"),
    ]
    conn.executemany(
        "INSERT INTO accounts(code, name, type, normal_balance) VALUES (?, ?, ?, ?)",
        accounts,
    )


# Helpers used across the app -------------------------------------------------

def list_accounts(conn=None, active_only=True):
    close = False
    if conn is None:
        conn = get_conn()
        close = True
    q = "SELECT * FROM accounts"
    if active_only:
        q += " WHERE active = 1"
    q += " ORDER BY code"
    rows = conn.execute(q).fetchall()
    if close:
        conn.close()
    return rows


def account_balance(conn, account_id, end_date=None):
    """Return signed balance: positive means a balance on the account's normal side.

    For an Asset (debit-normal): debits - credits.
    For a Liability/Equity/Income (credit-normal): credits - debits.
    Expenses (debit-normal): debits - credits.
    """
    acct = conn.execute("SELECT normal_balance FROM accounts WHERE id = ?", (account_id,)).fetchone()
    if not acct:
        return 0.0
    params = [account_id]
    where_date = ""
    if end_date:
        where_date = " AND t.date <= ?"
        params.append(end_date)
    row = conn.execute(
        f"""
        SELECT
            COALESCE(SUM(le.debit), 0) AS total_debit,
            COALESCE(SUM(le.credit), 0) AS total_credit
        FROM ledger_entries le
        JOIN transactions t ON t.id = le.transaction_id
        WHERE le.account_id = ?{where_date}
        """,
        params,
    ).fetchone()
    debit, credit = row["total_debit"], row["total_credit"]
    return (debit - credit) if acct["normal_balance"] == "debit" else (credit - debit)


def period_activity(conn, account_id, start_date, end_date):
    """Net activity for an account in a date range, signed on the normal side."""
    acct = conn.execute("SELECT normal_balance FROM accounts WHERE id = ?", (account_id,)).fetchone()
    if not acct:
        return 0.0
    row = conn.execute(
        """
        SELECT
            COALESCE(SUM(le.debit), 0) AS total_debit,
            COALESCE(SUM(le.credit), 0) AS total_credit
        FROM ledger_entries le
        JOIN transactions t ON t.id = le.transaction_id
        WHERE le.account_id = ? AND t.date >= ? AND t.date <= ?
        """,
        (account_id, start_date, end_date),
    ).fetchone()
    debit, credit = row["total_debit"], row["total_credit"]
    return (debit - credit) if acct["normal_balance"] == "debit" else (credit - debit)
