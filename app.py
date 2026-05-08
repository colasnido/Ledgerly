"""Ledgerly — Flask entry point.

Run:    python app.py
Visit:  http://127.0.0.1:5000
"""
import os
import uuid
from datetime import date, datetime
from pathlib import Path

from dotenv import load_dotenv
from flask import (
    Flask, render_template, request, redirect, url_for, flash, abort
)

import database as db_mod
from database import db, get_conn, list_accounts
import csv_import
import ai_categorize
import reports

load_dotenv()

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "dev-secret-change-me")
app.config["MAX_CONTENT_LENGTH"] = 5 * 1024 * 1024  # 5 MB CSV cap


# Jinja filter for currency display
@app.template_filter("money")
def money(v):
    if v is None:
        return ""
    try:
        v = float(v)
    except (TypeError, ValueError):
        return str(v)
    sign = "-" if v < 0 else ""
    return f"{sign}${abs(v):,.2f}"


@app.template_filter("today_str")
def today_str(_=None):
    return date.today().strftime("%Y-%m-%d")


# Initialize DB on startup
db_mod.init_db()


# ---------------------------------------------------------------- dashboard --
@app.route("/")
def dashboard():
    with db() as conn:
        recent = conn.execute(
            """
            SELECT t.id, t.date, t.description, t.reconciled,
                   COALESCE(SUM(le.debit), 0) AS total_debit
            FROM transactions t
            LEFT JOIN ledger_entries le ON le.transaction_id = t.id
            GROUP BY t.id
            ORDER BY t.date DESC, t.id DESC
            LIMIT 10
            """
        ).fetchall()
        counts = {
            "transactions": conn.execute("SELECT COUNT(*) AS n FROM transactions").fetchone()["n"],
            "pending_imports": conn.execute("SELECT COUNT(*) AS n FROM bank_imports WHERE posted=0").fetchone()["n"],
            "unreconciled": conn.execute("SELECT COUNT(*) AS n FROM transactions WHERE reconciled=0").fetchone()["n"],
        }
    return render_template("dashboard.html", recent=recent, counts=counts)


# ------------------------------------------------------------------ accounts --
@app.route("/accounts")
def accounts_page():
    accts = list_accounts()
    return render_template("accounts.html", accounts=accts)


@app.route("/accounts/new", methods=["POST"])
def accounts_new():
    code = request.form.get("code", "").strip()
    name = request.form.get("name", "").strip()
    acct_type = request.form.get("type", "")
    normal = request.form.get("normal_balance", "")
    if not (code and name and acct_type in ("Asset", "Liability", "Equity", "Income", "Expense")
            and normal in ("debit", "credit")):
        flash("Invalid account data.", "error")
        return redirect(url_for("accounts_page"))
    try:
        with db() as conn:
            conn.execute(
                "INSERT INTO accounts(code, name, type, normal_balance) VALUES (?, ?, ?, ?)",
                (code, name, acct_type, normal),
            )
        flash(f"Account {code} — {name} created.", "ok")
    except Exception as e:
        flash(f"Could not create account: {e}", "error")
    return redirect(url_for("accounts_page"))


# ------------------------------------------------------------- CSV import --
@app.route("/import", methods=["GET", "POST"])
def import_csv():
    if request.method == "POST":
        f = request.files.get("file")
        bank_account_id = request.form.get("bank_account_id", type=int)
        if not f or not f.filename:
            flash("Please choose a CSV file.", "error")
            return redirect(url_for("import_csv"))
        if not bank_account_id:
            flash("Please choose which bank account this CSV belongs to.", "error")
            return redirect(url_for("import_csv"))
        try:
            rows = csv_import.parse_bank_csv(f)
        except Exception as e:
            flash(f"Could not parse CSV: {e}", "error")
            return redirect(url_for("import_csv"))

        # Get AI suggestions
        accts = list_accounts()
        tx_for_ai = [{"index": i, "date": r["date"], "description": r["description"], "amount": r["amount"]}
                     for i, r in enumerate(rows)]
        suggestions = ai_categorize.categorize_transactions(tx_for_ai, accts)
        sugg_by_idx = {s["index"]: s for s in suggestions}

        # Stage in bank_imports
        batch_id = uuid.uuid4().hex[:12]
        with db() as conn:
            for i, r in enumerate(rows):
                s = sugg_by_idx.get(i, {})
                conn.execute(
                    """INSERT INTO bank_imports(batch_id, date, description, amount,
                                                suggested_account_id, suggestion_reason)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (batch_id, r["date"], r["description"], r["amount"],
                     s.get("account_id"), s.get("reason")),
                )
        # Stash bank_account_id for this batch via session-like flash
        flash(f"Imported {len(rows)} transactions. Review and confirm below.", "ok")
        return redirect(url_for("review_import", batch_id=batch_id, bank=bank_account_id))

    bank_accts = [a for a in list_accounts() if a["type"] == "Asset"]
    return render_template("import.html", bank_accts=bank_accts)


@app.route("/import/<batch_id>/review")
def review_import(batch_id):
    bank_account_id = request.args.get("bank", type=int)
    with db() as conn:
        rows = conn.execute(
            "SELECT * FROM bank_imports WHERE batch_id = ? AND posted = 0 ORDER BY date, id",
            (batch_id,),
        ).fetchall()
    if not rows:
        flash("No pending transactions in that batch.", "error")
        return redirect(url_for("import_csv"))
    accts = list_accounts()
    return render_template("review_import.html",
                           rows=rows, accounts=accts,
                           batch_id=batch_id, bank_account_id=bank_account_id)


@app.route("/import/<batch_id>/post", methods=["POST"])
def post_import(batch_id):
    bank_account_id = request.form.get("bank_account_id", type=int)
    if not bank_account_id:
        flash("Missing bank account.", "error")
        return redirect(url_for("import_csv"))

    with db() as conn:
        bank_acct = conn.execute("SELECT * FROM accounts WHERE id = ?", (bank_account_id,)).fetchone()
        if not bank_acct or bank_acct["type"] != "Asset":
            flash("Invalid bank account.", "error")
            return redirect(url_for("import_csv"))

        pending = conn.execute(
            "SELECT * FROM bank_imports WHERE batch_id = ? AND posted = 0",
            (batch_id,),
        ).fetchall()
        posted_count = 0
        skipped_count = 0

        for row in pending:
            field = f"account_{row['id']}"
            skip_field = f"skip_{row['id']}"
            if request.form.get(skip_field):
                skipped_count += 1
                continue
            account_id = request.form.get(field, type=int)
            if not account_id:
                continue

            amount = row["amount"]
            # Create transaction header
            cur = conn.execute(
                "INSERT INTO transactions(date, description, source) VALUES (?, ?, 'csv')",
                (row["date"], row["description"]),
            )
            tx_id = cur.lastrowid

            # Money INTO bank (positive amount): debit Bank, credit other account
            # Money OUT of bank (negative amount): credit Bank, debit other account
            if amount >= 0:
                bank_debit, bank_credit = amount, 0
                other_debit, other_credit = 0, amount
            else:
                bank_debit, bank_credit = 0, abs(amount)
                other_debit, other_credit = abs(amount), 0

            conn.execute(
                """INSERT INTO ledger_entries(transaction_id, account_id, debit, credit, memo)
                   VALUES (?, ?, ?, ?, ?)""",
                (tx_id, bank_account_id, bank_debit, bank_credit, row["description"]),
            )
            conn.execute(
                """INSERT INTO ledger_entries(transaction_id, account_id, debit, credit, memo)
                   VALUES (?, ?, ?, ?, ?)""",
                (tx_id, account_id, other_debit, other_credit, row["description"]),
            )
            conn.execute(
                "UPDATE bank_imports SET posted = 1, transaction_id = ? WHERE id = ?",
                (tx_id, row["id"]),
            )
            posted_count += 1

    flash(f"Posted {posted_count} transactions. Skipped {skipped_count}.", "ok")
    return redirect(url_for("transactions_page"))


# ----------------------------------------------------------- transactions --
@app.route("/transactions")
def transactions_page():
    start = request.args.get("start", "")
    end = request.args.get("end", "")
    with db() as conn:
        q = """
            SELECT t.*,
                   (SELECT COALESCE(SUM(debit), 0) FROM ledger_entries WHERE transaction_id = t.id) AS total
            FROM transactions t
        """
        params = []
        where = []
        if start:
            where.append("t.date >= ?")
            params.append(start)
        if end:
            where.append("t.date <= ?")
            params.append(end)
        if where:
            q += " WHERE " + " AND ".join(where)
        q += " ORDER BY t.date DESC, t.id DESC LIMIT 500"
        txs = conn.execute(q, params).fetchall()
    return render_template("transactions.html", transactions=txs, start=start, end=end)


@app.route("/transactions/<int:tx_id>")
def transaction_detail(tx_id):
    with db() as conn:
        tx = conn.execute("SELECT * FROM transactions WHERE id = ?", (tx_id,)).fetchone()
        if not tx:
            abort(404)
        entries = conn.execute(
            """SELECT le.*, a.code, a.name AS account_name
               FROM ledger_entries le JOIN accounts a ON a.id = le.account_id
               WHERE le.transaction_id = ? ORDER BY le.id""",
            (tx_id,),
        ).fetchall()
    return render_template("transaction_detail.html", tx=tx, entries=entries)


@app.route("/transactions/<int:tx_id>/delete", methods=["POST"])
def transaction_delete(tx_id):
    with db() as conn:
        conn.execute("DELETE FROM transactions WHERE id = ?", (tx_id,))
    flash("Transaction deleted.", "ok")
    return redirect(url_for("transactions_page"))


# ------------------------------------------------------- journal entry --
@app.route("/journal/new", methods=["GET", "POST"])
def journal_new():
    accts = list_accounts()
    if request.method == "POST":
        date_str = request.form.get("date", "").strip()
        desc = request.form.get("description", "").strip()
        # Collect lines: account_id_N, debit_N, credit_N
        lines = []
        for key in request.form:
            if key.startswith("account_id_"):
                idx = key.split("_")[-1]
                aid = request.form.get(f"account_id_{idx}", type=int)
                debit = float(request.form.get(f"debit_{idx}") or 0)
                credit = float(request.form.get(f"credit_{idx}") or 0)
                if aid and (debit > 0 or credit > 0):
                    lines.append((aid, debit, credit))
        if not date_str or not desc:
            flash("Date and description are required.", "error")
            return render_template("journal_new.html", accounts=accts)
        if len(lines) < 2:
            flash("A journal entry needs at least 2 lines.", "error")
            return render_template("journal_new.html", accounts=accts)
        total_debit = sum(l[1] for l in lines)
        total_credit = sum(l[2] for l in lines)
        if abs(total_debit - total_credit) > 0.005:
            flash(f"Debits ({total_debit:.2f}) must equal credits ({total_credit:.2f}).", "error")
            return render_template("journal_new.html", accounts=accts)

        with db() as conn:
            cur = conn.execute(
                "INSERT INTO transactions(date, description, source) VALUES (?, ?, 'manual')",
                (date_str, desc),
            )
            tx_id = cur.lastrowid
            for aid, debit, credit in lines:
                conn.execute(
                    "INSERT INTO ledger_entries(transaction_id, account_id, debit, credit) VALUES (?, ?, ?, ?)",
                    (tx_id, aid, debit, credit),
                )
        flash("Journal entry posted.", "ok")
        return redirect(url_for("transaction_detail", tx_id=tx_id))

    return render_template("journal_new.html", accounts=accts)


# -------------------------------------------------------- reconciliation --
@app.route("/reconcile", methods=["GET", "POST"])
def reconcile_page():
    if request.method == "POST":
        ids = request.form.getlist("reconcile")
        action = request.form.get("action", "mark_cleared")
        if ids:
            placeholders = ",".join("?" * len(ids))
            value = 1 if action == "mark_cleared" else 0
            with db() as conn:
                conn.execute(
                    f"UPDATE transactions SET reconciled = ? WHERE id IN ({placeholders})",
                    [value, *ids],
                )
            flash(f"Updated {len(ids)} transaction(s).", "ok")
        return redirect(url_for("reconcile_page"))

    show = request.args.get("show", "unreconciled")
    with db() as conn:
        q = """
            SELECT t.*,
                   (SELECT COALESCE(SUM(debit), 0) FROM ledger_entries WHERE transaction_id = t.id) AS total
            FROM transactions t
        """
        if show == "unreconciled":
            q += " WHERE t.reconciled = 0"
        elif show == "reconciled":
            q += " WHERE t.reconciled = 1"
        q += " ORDER BY t.date DESC, t.id DESC LIMIT 500"
        txs = conn.execute(q).fetchall()
    return render_template("reconcile.html", transactions=txs, show=show)


# ------------------------------------------------------------- reports --
@app.route("/reports/income-statement")
def income_statement_view():
    today = date.today()
    start = request.args.get("start") or today.replace(day=1).strftime("%Y-%m-%d")
    end = request.args.get("end") or today.strftime("%Y-%m-%d")
    data = reports.income_statement(start, end)
    return render_template("income_statement.html", r=data)


@app.route("/reports/balance-sheet")
def balance_sheet_view():
    today = date.today()
    as_of = request.args.get("as_of") or today.strftime("%Y-%m-%d")
    data = reports.balance_sheet(as_of)
    return render_template("balance_sheet.html", r=data)


if __name__ == "__main__":
    app.run(debug=True, port=5000)
