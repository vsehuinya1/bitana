"""
Secrets loader — reads API keys from .env file.
Never hardcode credentials.
"""
import os
from pathlib import Path
from dotenv import load_dotenv

# Load .env from research root
_env_path = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(_env_path)


def get_secrets() -> dict:
    """Return dict of all API credentials."""
    return {
        "binance_api_key": os.getenv("BINANCE_API_KEY", ""),
        "binance_secret_key": os.getenv("BINANCE_SECRET_KEY", ""),
        "coinalyze_api_key": os.getenv("COINALYZE_API_KEY", ""),
    }


def get_binance_keys() -> tuple:
    """Return (api_key, secret_key) for Binance."""
    s = get_secrets()
    return s["binance_api_key"], s["binance_secret_key"]


def get_coinalyze_key() -> str:
    """Return Coinalyze API key."""
    return get_secrets()["coinalyze_api_key"]
