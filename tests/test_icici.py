import os
import pandas as pd

def test_icici_output_exists():
    assert os.path.exists("result.csv"), "Result CSV not found"

def test_icici_output_format():
    df = pd.read_csv("result.csv")
    expected_cols = ["Date", "Description", "Debit Amt", "Credit Amt", "Balance"]
    assert list(df.columns) == expected_cols, "CSV columns do not match"
