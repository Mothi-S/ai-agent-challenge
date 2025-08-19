import pdfplumber
import pandas as pd

def extract_text(pdf_path):
    rows = []
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            text = page.extract_text()
            if text:
                lines = text.split("\n")
                for line in lines:
                    parts = line.split()
                    # Parse rows if they match Date + Description + Debit + Credit + Balance
                    if len(parts) >= 5 and parts[0] != "Date":
                        date = parts[0]
                        desc = " ".join(parts[1:-3])
                        debit = parts[-3]
                        credit = parts[-2]
                        balance = parts[-1]
                        rows.append([date, desc, debit, credit, balance])

    df = pd.DataFrame(rows, columns=["Date", "Description", "Debit Amt", "Credit Amt", "Balance"])

    # Remove duplicate rows
    df = df.drop_duplicates().reset_index(drop=True)

    return df

