#!/usr/bin/env python3
"""Transfer USDT from Binance spot wallet to USDT-M futures (mainnet)."""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import hmac
import os
import sys
import time
from pathlib import Path
from urllib.parse import urlencode

import aiohttp
from dotenv import load_dotenv

SPOT_BASE = "https://api.binance.com"


def _sign(secret: str, params: dict) -> dict:
    params = dict(params)
    params["timestamp"] = int(time.time() * 1000)
    query = urlencode(params)
    params["signature"] = hmac.new(
        secret.encode(), query.encode(), hashlib.sha256
    ).hexdigest()
    return params


async def _get(session: aiohttp.ClientSession, path: str, params: dict, key: str, secret: str) -> dict:
    signed = _sign(secret, params)
    headers = {"X-MBX-APIKEY": key}
    async with session.get(f"{SPOT_BASE}{path}", params=signed, headers=headers) as resp:
        data = await resp.json()
        if resp.status >= 400:
            raise RuntimeError(f"GET {path} failed ({resp.status}): {data}")
        return data


async def _post(session: aiohttp.ClientSession, path: str, params: dict, key: str, secret: str) -> dict:
    signed = _sign(secret, params)
    headers = {"X-MBX-APIKEY": key}
    async with session.post(f"{SPOT_BASE}{path}", data=signed, headers=headers) as resp:
        data = await resp.json()
        if resp.status >= 400:
            raise RuntimeError(f"POST {path} failed ({resp.status}): {data}")
        return data


async def main() -> int:
    parser = argparse.ArgumentParser(description="Transfer USDT spot → USDT-M futures")
    parser.add_argument("--amount", type=float, required=True, help="USDT amount to transfer")
    parser.add_argument("--asset", default="USDT")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    root = Path(__file__).resolve().parents[1]
    load_dotenv(root / ".env")
    key = os.getenv("BINANCE_API_KEY", "")
    secret = os.getenv("BINANCE_API_SECRET", "")
    testnet = os.getenv("BINANCE_TESTNET", "true").lower() in ("1", "true", "yes")
    if testnet:
        print("ERROR: BINANCE_TESTNET is true — set false for mainnet transfer", file=sys.stderr)
        return 1
    if not key or not secret:
        print("ERROR: missing BINANCE_API_KEY / BINANCE_API_SECRET in .env", file=sys.stderr)
        return 1

    async with aiohttp.ClientSession() as session:
        account = await _get(session, "/api/v3/account", {}, key, secret)
        free = 0.0
        for bal in account.get("balances", []):
            if bal.get("asset") == args.asset:
                free = float(bal.get("free", 0))
                break
        print(f"Spot {args.asset} free: {free:.4f}")

        if free < args.amount:
            print(f"ERROR: insufficient spot balance ({free:.4f} < {args.amount})", file=sys.stderr)
            return 1

        if args.dry_run:
            print(f"DRY RUN: would transfer {args.amount} {args.asset} MAIN → UMFUTURE")
            return 0

        result = await _post(
            session,
            "/sapi/v1/asset/transfer",
            {
                "type": "MAIN_UMFUTURE",
                "asset": args.asset,
                "amount": f"{args.amount:.8f}".rstrip("0").rstrip("."),
            },
            key,
            secret,
        )
        print(f"Transfer OK: tranId={result.get('tranId')} amount={args.amount} {args.asset}")
        return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
