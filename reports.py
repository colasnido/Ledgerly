"""Financial reports: Income Statement and Balance Sheet."""
from database import get_conn, period_activity, account_balance


def income_statement(start_date, end_date):
    """Return dict: {revenue: [(account, amount)], expenses: [...], totals, net_income}."""
    conn = get_conn()
    try:
        income_accts = conn.execute(
            "SELECT * FROM accounts WHERE type='Income' AND active=1 ORDER BY code"
        ).fetchall()
        expense_accts = conn.execute(
            "SELECT * FROM accounts WHERE type='Expense' AND active=1 ORDER BY code"
        ).fetchall()

        revenue_lines = []
        total_revenue = 0.0
        for a in income_accts:
            amt = period_activity(conn, a["id"], start_date, end_date)
            if abs(amt) > 0.005:
                revenue_lines.append({"code": a["code"], "name": a["name"], "amount": amt})
                total_revenue += amt

        expense_lines = []
        total_expenses = 0.0
        for a in expense_accts:
            amt = period_activity(conn, a["id"], start_date, end_date)
            if abs(amt) > 0.005:
                expense_lines.append({"code": a["code"], "name": a["name"], "amount": amt})
                total_expenses += amt

        return {
            "start_date": start_date,
            "end_date": end_date,
            "revenue_lines": revenue_lines,
            "total_revenue": total_revenue,
            "expense_lines": expense_lines,
            "total_expenses": total_expenses,
            "net_income": total_revenue - total_expenses,
        }
    finally:
        conn.close()


def balance_sheet(as_of_date):
    """Return dict with assets, liabilities, equity grouped, plus totals.

    Net income for all periods up to as_of_date flows into Retained Earnings
    so the equation balances: Assets = Liabilities + Equity.
    """
    conn = get_conn()
    try:
        asset_accts = conn.execute(
            "SELECT * FROM accounts WHERE type='Asset' AND active=1 ORDER BY code"
        ).fetchall()
        liability_accts = conn.execute(
            "SELECT * FROM accounts WHERE type='Liability' AND active=1 ORDER BY code"
        ).fetchall()
        equity_accts = conn.execute(
            "SELECT * FROM accounts WHERE type='Equity' AND active=1 ORDER BY code"
        ).fetchall()

        def lines(accts):
            out, total = [], 0.0
            for a in accts:
                bal = account_balance(conn, a["id"], end_date=as_of_date)
                if abs(bal) > 0.005:
                    out.append({"code": a["code"], "name": a["name"], "amount": bal})
                    total += bal
            return out, total

        asset_lines, total_assets = lines(asset_accts)
        liability_lines, total_liabilities = lines(liability_accts)
        equity_lines, total_equity_recorded = lines(equity_accts)

        # Compute net income from start of time to as_of_date and roll into "Current Period Earnings"
        income_accts = conn.execute(
            "SELECT id FROM accounts WHERE type='Income' AND active=1"
        ).fetchall()
        expense_accts = conn.execute(
            "SELECT id FROM accounts WHERE type='Expense' AND active=1"
        ).fetchall()
        revenue_total = sum(period_activity(conn, a["id"], "0000-01-01", as_of_date) for a in income_accts)
        expense_total = sum(period_activity(conn, a["id"], "0000-01-01", as_of_date) for a in expense_accts)
        current_earnings = revenue_total - expense_total

        if abs(current_earnings) > 0.005:
            equity_lines.append({
                "code": "—",
                "name": "Current Period Earnings",
                "amount": current_earnings,
            })
        total_equity = total_equity_recorded + current_earnings

        return {
            "as_of_date": as_of_date,
            "asset_lines": asset_lines,
            "total_assets": total_assets,
            "liability_lines": liability_lines,
            "total_liabilities": total_liabilities,
            "equity_lines": equity_lines,
            "total_equity": total_equity,
            "total_liab_and_equity": total_liabilities + total_equity,
            "balanced": abs(total_assets - (total_liabilities + total_equity)) < 0.01,
        }
    finally:
        conn.close()
