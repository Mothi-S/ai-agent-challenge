# agent.py
from __future__ import annotations


import argparse
import os
import subprocess
import sys
from pathlib import Path
from typing import Tuple, List, Optional

import pandas as pd
from utils.pdf import extract_text

AGENT_MAX_ATTEMPTS = 3
PARSER_DIR = Path("custom_parsers")

TEMPLATE_HEADER = '''from __future__ import annotations
import re
import pandas as pd
from utils.pdf import extract_text

def parse(pdf_path: str) -> pd.DataFrame:
    """
    Parse bank statement PDF at pdf_path and return a DataFrame exactly matching
    the expected CSV schema for this bank.
    """
    text = extract_text(pdf_path)
    # Keep non-empty lines only
    lines = [ln.strip() for ln in text.splitlines() if ln and ln.strip()]
    # BEGIN PARSE LOGIC
{body}
    # END PARSE LOGIC
'''

def _pick(cols: List[str], names: List[str]) -> Optional[str]:
    for n in names:
        if n in cols:
            return n
    return None

def guess_parser_body(expected_df: pd.DataFrame, pdf_text: str) -> str:
    # Expected columns and canonical mappings
    cols = list(expected_df.columns)

    col_date    = _pick(cols, ["Date", "Txn Date", "Transaction Date"])
    col_desc    = _pick(cols, ["Description", "Narration", "Details"])
    col_debit   = _pick(cols, ["Debit Amt", "Debit", "Withdrawal", "Dr"])
    col_credit  = _pick(cols, ["Credit Amt", "Credit", "Deposit", "Cr"])
    col_amount  = _pick(cols, ["Amount", "Amt"])
    col_balance = _pick(cols, ["Balance", "Closing Balance", "Bal"])

    # numeric columns present
    num_cols = [c for c in [col_debit, col_credit, col_amount, col_balance] if c]

    # Decide whether statement likely uses CR/DR flags by peeking the text
    uses_type = (" CR " in pdf_text) or (" DR " in pdf_text) or ("Cr " in pdf_text) or ("Dr " in pdf_text)

    # Code that will run inside the generated parser
    code = f"""    import re as _re
    import datetime as _dt
    import pandas as _pd

    col_date    = {repr(col_date)}
    col_desc    = {repr(col_desc)}
    col_debit   = {repr(col_debit)}
    col_credit  = {repr(col_credit)}
    col_amount  = {repr(col_amount)}
    col_balance = {repr(col_balance)}
    expected_cols = {cols!r}
    num_cols = {num_cols!r}
    uses_type = {uses_type!r}

    # ---- Helpers ----
    def _norm_date(raw: str) -> str:
        raw = raw.strip()
        for fmt in ('%d-%m-%Y','%d/%m/%Y','%Y-%m-%d','%Y/%m/%d'):
            try:
                return _dt.datetime.strptime(raw, fmt).date().isoformat()
            except Exception:
                pass
        return raw

    def _num(s: str) -> float:
        # Handle commas and parentheses (negative)
        s = s.replace(',', '').strip()
        if s == '' or s is None:
            return 0.0
        neg = False
        if s.startswith('(') and s.endswith(')'):
            neg = True
            s = s[1:-1]
        try:
            v = float(s)
        except Exception:
            v = 0.0
        return -v if neg else v

    date_pat1 = r"(?P<Date>\\d{{2}}[-/]\\d{{2}}[-/]\\d{{4}})"
    date_pat2 = r"(?P<Date>\\d{{4}}[-/]\\d{{2}}[-/]\\d{{2}})"
    type_pat  = r"(?P<Type>[Cc][Rr]|[Dd][Rr])"
    money_pat = r"(?P<Amt>[\\(\\)]?(?:\\d{{1,3}}(?:,\\d{{3}})*|\\d+)(?:\\.\\d{{2}})?[\\(\\)]?)"
    bal_pat   = r"(?P<Bal>[\\(\\)]?(?:\\d{{1,3}}(?:,\\d{{3}})*|\\d+)(?:\\.\\d{{2}})?[\\(\\)]?)"

    # CR/DR style patterns
    _pats_type = [
        _re.compile(rf"^{{date_pat1}}\\s+(?P<Description>.+?)\\s+{{type_pat}}\\s+{{money_pat}}\\s+{{bal_pat}}$", _re.IGNORECASE),
        _re.compile(rf"^{{date_pat2}}\\s+(?P<Description>.+?)\\s+{{type_pat}}\\s+{{money_pat}}\\s+{{bal_pat}}$", _re.IGNORECASE),
        _re.compile(rf"^{{date_pat1}}\\s+(?P<Description>.+?)\\s+{{money_pat}}\\s+{{type_pat}}\\s+{{bal_pat}}$", _re.IGNORECASE),
    ]

    # Tabular style (no CR/DR), typically: Date  Desc  Debit  Credit  Balance   OR  Date  Desc  Credit  Debit  Balance
    # We will split by 2+ spaces, then infer numeric cells at the end.
    def _try_rows(lines):
        rows = []
        for ln in lines:
            matched = False

            # 1) Try CR/DR patterns if statement uses type
            if uses_type:
                for pat in _pats_type:
                    m = pat.match(ln)
                    if m:
                        d = m.groupdict()
                        row = {{c: None for c in expected_cols}}
                        if col_date:    row[col_date] = _norm_date(d.get('Date',''))
                        if col_desc:    row[col_desc] = d.get('Description','').strip()
                        amt = _num(d.get('Amt','0'))
                        bal = _num(d.get('Bal','0'))
                        t   = (d.get('Type','') or '').upper()
                        if col_amount and (col_debit is None or col_credit is None):
                            # single Amount column schemas
                            row[col_amount] = amt
                        else:
                            if t == 'CR':
                                if col_credit: row[col_credit] = amt
                                if col_debit:  row[col_debit]  = 0.0
                            elif t == 'DR':
                                if col_debit:  row[col_debit]  = amt
                                if col_credit: row[col_credit] = 0.0
                        if col_balance: row[col_balance] = bal

                        # defaults
                        for nc in num_cols:
                            if nc and row[nc] is None:
                                row[nc] = 0.0
                        rows.append(row)
                        matched = True
                        break
                if matched:
                    continue

            # 2) Fallback: table-like lines split by >=2 spaces
            parts = _re.split(r"\\s{{2,}}", ln.strip())
            # Expect at least: Date, Desc, [Debit/Credit/Amount...], Balance
            if len(parts) >= 3:
                # First token must be date-ish
                if _re.match(r"^(?:\\d{{2}}[-/]\\d{{2}}[-/]\\d{{4}}|\\d{{4}}[-/]\\d{{2}}[-/]\\d{{2}})$", parts[0].strip()):
                    row = {{c: None for c in expected_cols}}
                    if col_date: row[col_date] = _norm_date(parts[0].strip())
                    if col_desc: row[col_desc] = (parts[1] if len(parts) > 1 else '').strip()

                    # Gather numeric tokens from the tail
                    nums = []
                    for p in parts[2:]:
                        p2 = p.strip()
                        # allow amounts like "1,234.00" or "(1,234.00)"
                        if _re.match(r"^[\\(\\)]?(?:\\d{{1,3}}(?:,\\d{{3}})*|\\d+)(?:\\.\\d{{2}})?[\\(\\)]?$", p2):
                            nums.append(_num(p2))

                    # Heuristics:
                    # if 3+ numbers, assume last is balance; the preceding two are (debit, credit) in some order (or one is 0.0)
                    if len(nums) >= 1 and col_balance:
                        row[col_balance] = nums[-1]
                    # assign debit/credit/amount depending on schema
                    remaining = nums[:-1] if len(nums) >= 1 else nums[:]

                    if col_amount and not (col_debit or col_credit):
                        # single amount column schema: pick first numeric if present
                        if remaining:
                            row[col_amount] = remaining[0]
                    else:
                        # two-column schema: Debit & Credit (one of them often 0.00)
                        # try to infer from description keywords; otherwise treat first non-zero as the active side
                        desc = (row.get(col_desc) or '').lower() if col_desc else ''
                        credit_keywords = ('salary','interest','refund','cashback','reversal','credit','neft in','upi in')
                        debit_keywords  = ('withdrawal','atm','payment','debit','imps','upi','pos','recharge','emi','transfer out','purchase')

                        # initialize defaults
                        if col_debit and row[col_debit] is None:   row[col_debit] = 0.0
                        if col_credit and row[col_credit] is None: row[col_credit] = 0.0

                        if remaining:
                            # Common case: exactly 2 numbers before balance
                            if len(remaining) >= 2:
                                a, b = remaining[0], remaining[1]
                                # If one of them is zero, map the non-zero by heuristics
                                if abs(a) < 1e-9 and abs(b) > 1e-9:
                                    # only b is present
                                    if any(k in desc for k in credit_keywords):
                                        if col_credit: row[col_credit] = b
                                    elif any(k in desc for k in debit_keywords):
                                        if col_debit: row[col_debit] = b
                                    else:
                                        # fallback: assume credit if positive wording else debit
                                        if col_debit: row[col_debit] = b
                                elif abs(b) < 1e-9 and abs(a) > 1e-9:
                                    if any(k in desc for k in credit_keywords):
                                        if col_credit: row[col_credit] = a
                                    elif any(k in desc for k in debit_keywords):
                                        if col_debit: row[col_debit] = a
                                    else:
                                        if col_debit: row[col_debit] = a
                                else:
                                    # both non-zero (rare) -> prefer debit if negative, else infer by keywords
                                    if a < 0 or b < 0:
                                        if col_debit: row[col_debit] = abs(a if a < 0 else b)
                                    else:
                                        if any(k in desc for k in credit_keywords):
                                            if col_credit: row[col_credit] = max(a, b)
                                        elif any(k in desc for k in debit_keywords):
                                            if col_debit: row[col_debit] = max(a, b)
                                        else:
                                            # fallback: first -> debit
                                            if col_debit: row[col_debit] = a
                                            if col_credit: row[col_credit] = 0.0
                            else:
                                # Only one amount before balance
                                val = remaining[0]
                                if any(k in desc for k in credit_keywords):
                                    if col_credit: row[col_credit] = val
                                elif any(k in desc for k in debit_keywords):
                                    if col_debit: row[col_debit] = val
                                else:
                                    # fallback: assume debit
                                    if col_debit: row[col_debit] = val

                    # defaults for numeric columns
                    for nc in num_cols:
                        if nc and row[nc] is None:
                            row[nc] = 0.0

                    # keep if at least one numeric present
                    if any((row.get(c) or 0.0) != 0.0 for c in num_cols if c):
                        rows.append(row)

        return rows

    rows = _try_rows(lines)
    df = _pd.DataFrame(rows, columns=expected_cols)
    return df
"""
    return code

def write_parser(target: str, code: str) -> str:
    PARSER_DIR.mkdir(exist_ok=True, parents=True)
    path = PARSER_DIR / f"{target}_parser.py"
    path.write_text(code, encoding="utf-8")
    return str(path)

def run_pytest(target: str) -> Tuple[bool, str]:
    env = os.environ.copy()
    env["TARGET"] = target
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-q"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        env=env,
        text=True,
    )
    ok = proc.returncode == 0
    return ok, proc.stdout

def refine_from_failure(log: str, prev_code: str, expected_df: pd.DataFrame) -> str:
    """
    Small automatic fixes between attempts:
    - If got empty rows, relax the CR/DR assumption.
    - Ensure IGNORECASE in regex and widen numeric matching.
    """
    patched = prev_code

    # If tests say parsed DF != expected and the printed DF looked empty, try forcing uses_type=False
    if "Empty DataFrame" in log and "Columns:" in log:
        patched = patched.replace("uses_type = True", "uses_type = False")

    # Make sure regex has IGNORECASE (already in code), widen money pattern if needed
    # (No-op here because templates already include IGNORECASE and wide patterns)

    return patched

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", required=True, help="Bank target folder, e.g., icici or sbi")
    args = ap.parse_args()
    target = args.target.lower()

    data_dir = Path("data") / target
    if not data_dir.exists():
        print(f"[ERR] Missing data for target: {target} at {data_dir}", file=sys.stderr)
        sys.exit(1)

    # Locate one PDF and one CSV in data/<target>/
    pdf_path = None
    csv_path = None
    for fn in data_dir.iterdir():
        if fn.suffix.lower() == ".pdf" and pdf_path is None:
            pdf_path = str(fn)
        if fn.suffix.lower() == ".csv" and csv_path is None:
            csv_path = str(fn)

    if not pdf_path or not csv_path:
        print("[ERR] Could not find both PDF and CSV in", data_dir, file=sys.stderr)
        sys.exit(1)

    try:
        expected = pd.read_csv(csv_path)
    except pd.errors.EmptyDataError:
        print(f"[ERR] CSV is empty or invalid: {csv_path}", file=sys.stderr)
        sys.exit(2)

    pdf_text = extract_text(pdf_path)

    # Initial generation
    code_body = guess_parser_body(expected, pdf_text)
    code = TEMPLATE_HEADER.format(body=code_body)
    parser_path = write_parser(target, code)

    # Attempt loop
    for attempt in range(1, AGENT_MAX_ATTEMPTS + 1):
        print(f"[Agent] Attempt {attempt} — running tests...")
        ok, out = run_pytest(target)
        print(out)
        if ok:
            print(f"[Agent] Success ✅ — parser at {parser_path}")
            sys.exit(0)
        # refine and retry
        code = refine_from_failure(out, code, expected)
        parser_path = write_parser(target, code)

    print("[Agent] Failed after max attempts ❌", file=sys.stderr)
    sys.exit(2)

if __name__ == "__main__":
    df = extract_text("data/icici/icici sample.pdf")

    df.to_csv("result.csv",index=False)
    print("✅ result.csv generated successfully")