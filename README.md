# Ledgerly

> Books, kept light.

A self-hosted, double-entry bookkeeping web app for small businesses and freelancers. Import a bank-statement CSV, let AI suggest a chart-of-accounts category for each transaction, confirm with one click, and get real Income Statement and Balance Sheet reports — all running locally on your machine.

No signup. No cloud. No telemetry.

---

## Contents

- [Highlights](#highlights)
- [Tech stack](#tech-stack)
- [Screenshots](#screenshots)
- [Quick start](#quick-start)
- [Configuration](#configuration)
- [Usage](#usage)
- [Project structure](#project-structure)
- [How the bookkeeping works](#how-the-bookkeeping-works)
- [Roadmap](#roadmap)
- [Troubleshooting](#troubleshooting)
- [License](#license)

## Highlights

- **Real double-entry accounting.** Every transaction posts balanced debit/credit pairs to a proper general ledger. The Balance Sheet actually balances (Assets = Liabilities + Equity), and current-period earnings roll into equity automatically.
- **AI-assisted categorization.** Google Gemini's free tier suggests the right chart-of-accounts category for each imported transaction. You always review and confirm before anything is posted.
- **Tolerant CSV import.** Works with most bank export formats out of the box — `Amount` columns or `Debit`/`Credit` pairs, multiple date formats, currency symbols, parentheses for negatives.
- **Reports that look like reports.** Income Statement (any date range) and Balance Sheet (as-of any date), with one-click print / save-as-PDF.
- **Local-first.** Runs on `127.0.0.1:5000` against a single SQLite file. Your data never leaves your machine — except the AI categorization step, which sends transaction descriptions to Google.
- **Clean modern UI.** Sidebar navigation, tabular numerals, semantic colors. No JavaScript framework, no build step — just Flask + Jinja2 + hand-written CSS.

## Tech stack

| Layer | Tooling |
|---|---|
| Backend | Python 3.10+, Flask 3 |
| Database | SQLite (single file, no server) |
| Frontend | Jinja2 templates, vanilla CSS, inline SVG icons, Inter via Google Fonts |
| AI | Google Gemini 2.0 Flash via `google-genai` SDK (free tier — 1,500 req/day) |
| Data | pandas (CSV parsing only) |

## Screenshots

> Add screenshots of your dashboard, import-review, and reports pages here once you've run the app and captured them. Suggested layout:
>
> ```
> docs/screenshots/dashboard.png
> ```

## Quick start

Requires Python 3.10 or newer.

```bash
git clone https://github.com/YOUR-USERNAME/ledgerly.git
cd ledgerly
pip install -r requirements.txt
cp .env.example .env       # Windows PowerShell: Copy-Item .env.example .env
python app.py
```

Open **http://127.0.0.1:5000** in your browser. Press `Ctrl+C` in the terminal to stop the server.

## Configuration

Ledgerly reads two environment variables from `.env`:

| Variable | Required | What it's for |
|---|:-:|---|
| `GOOGLE_API_KEY` | optional | Google AI Studio key for AI categorization. Without it, AI suggestions are skipped and you choose categories manually — everything else still works. |
| `FLASK_SECRET_KEY` | recommended | Random string used for Flask session cookies. Change from the default if you ever expose this beyond localhost. |

### Get a free Google AI Studio key

1. Go to **https://aistudio.google.com/apikey**
2. Sign in with a Google account
3. Click **Create API key** and paste the value into `.env`

The free tier covers 1,500 requests per day. One CSV import = one API request regardless of how many rows are in the file, so this is plenty for personal or small-business use.

## Usage

The repo ships with a sample bank statement at [`sample_data/sample_bank_statement.csv`](sample_data/sample_bank_statement.csv). To try the full flow:

1. Start the server (`python app.py`) and open http://127.0.0.1:5000
2. Click **Import CSV** in the sidebar
3. Pick **1000 Cash - Operating** as the bank account
4. Upload `sample_data/sample_bank_statement.csv`
5. Review the AI's category suggestions — override anything you disagree with, or check **skip** to leave a row unposted
6. Click **Post selected** — each row is written as a balanced double-entry journal
7. Open **Reports → Income Statement** and **Balance Sheet** to see the results

For ad-hoc adjustments (depreciation, owner contributions, error corrections), use **Journal Entry** — Ledgerly enforces that debits equal credits before posting.

## Project structure

```
ledgerly/
├── app.py              # Flask app + all routes
├── database.py         # SQLite schema, helpers, seeded chart of accounts
├── csv_import.py       # Tolerant bank-statement CSV parser
├── ai_categorize.py    # Gemini-powered category suggestions
├── reports.py          # Income Statement + Balance Sheet calculations
├── requirements.txt
├── .env.example
├── templates/          # Jinja2 pages
├── static/style.css    # Hand-written CSS
└── sample_data/
    └── sample_bank_statement.csv
```

The SQLite database (`bookkeeping.db`) is created automatically on first run. To reset, stop the server, delete the file, and restart — the chart of accounts re-seeds.

## How the bookkeeping works

For anyone unfamiliar with double-entry accounting:

- Every transaction is a list of **ledger entries** touching two or more accounts
- Each entry is a **debit** or a **credit**, and the totals must be equal
- A bank deposit becomes: *Debit Cash $X / Credit Sales Revenue $X*
- A bill payment becomes: *Credit Cash $X / Debit Rent Expense $X*

This is the same model QuickBooks, Xero, and every serious accounting system uses — it's what makes a real Balance Sheet possible. The schema:

```
accounts          chart of accounts (id, code, name, type, normal_balance)
transactions      logical events (id, date, description, source, reconciled)
ledger_entries    debit/credit lines (transaction_id, account_id, debit, credit)
bank_imports      staging area for CSV rows before they're posted
```

The seeded chart of accounts has 28 common small-business accounts across all five types (Asset / Liability / Equity / Income / Expense). You can add more from the **Chart of Accounts** page.

## Roadmap

Not built yet, but on the list:

- [ ] Edit / deactivate accounts from the UI (currently create-only)
- [ ] Edit transactions (currently delete-only)
- [ ] Native PDF export (currently uses browser print-to-PDF)
- [ ] Multi-currency support
- [ ] User authentication (for multi-user deployments)
- [ ] AI-assisted reconciliation matching
- [ ] Bank connections via Plaid
- [ ] Multi-business / multi-tenant support
- [ ] Test suite

Contributions welcome — open an issue or PR.

## Troubleshooting

| Symptom | Fix |
|---|---|
| `127.0.0.1:5000` won't load | The server isn't running. Run `python app.py` and leave the terminal open. |
| `ModuleNotFoundError: No module named 'flask'` | Run `pip install -r requirements.txt` |
| `Address already in use` on port 5000 | Edit the bottom of `app.py` to use `port=5001`, or close the other app. |
| AI suggestions always say "No API key set" | `.env` still has the placeholder. Put a real `GOOGLE_API_KEY=` value in and restart. |
| Want to reset the database | Stop the server, delete `bookkeeping.db`, restart. |

## License

This project is currently unlicensed (all rights reserved by default). If you'd like to use, modify, or distribute it, please open an issue. A permissive license (MIT) may be added in the future.
