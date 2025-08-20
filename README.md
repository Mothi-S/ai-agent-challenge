# Karbon AI Challenge — Agent-as-Coder Bank Statement Parser

## Overview
This project implements an **agent-as-coder** system that automatically generates parsers for bank statement PDFs.  
Given a target (e.g., `icici`), the agent observes a sample PDF and expected CSV, generates a parser under `custom_parsers/`, runs tests, and self-corrects if needed. The generated parser produces a CSV identical to the expected output.

---

## Quick 5-Step Run Instructions

1. **Clone the repo**
   ```bash
   git clone <your-fork-url>
   cd ai-agent-challenge

2. **Create & activate virtual environment, install dependencies**

    python -m venv .venv
    # Windows
    .venv\Scripts\activate
    # macOS / Linux
    # source .venv/bin/activate

    pip install -r requirements.txt

3. **Place one PDF and one expected CSV into data/<bank>/:**

    data/icici/icici sample.pdf
    data/icici/result.csv

4. **Run the agent to auto-generate the parser:**

    python agent.py --target icici

    On success you'll see:

    ✅ result.csv generated successfully
    [Agent] Success ✅ — parser at custom_parsers/icici_parser.py


5. **Run tests:**

    pytest -q

    You should see:

    2 passed in 2.XXs

6. **Requirements**

    pandas
    pdfplumber
    pytest


## Agent Design (One Paragraph)

The agent follows a simple loop: 
(1) observe — read data/<target>/ to locate one PDF and one expected CSV and extract a small sample of the PDF text; 
(2) plan & generate — synthesize a parser implementation template tailored to the CSV schema and visible PDF patterns (CR/DR tokens, tabular rows); 
(3) act — write custom_parsers/<target>_parser.py and run pytest to validate the parser output equals the expected CSV; 
(4) reflect — inspect the pytest failure output and apply small targeted fixes (e.g., relax regex, switch parsing mode, add IGNORECASE); 
(5) repeat up to 3 attempts then either succeed or save diagnostics for manual inspection. The generated parser implements parse(pdf_path) -> pd.DataFrame to match the expected CSV schema.


---

