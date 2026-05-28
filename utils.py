# utils.py — lightweight helpers with no heavy dependencies


def normalize_ticker(ticker: str) -> str:
    return str(ticker).strip().upper()
