# Bitana — Claude Code ground rules (READ FIRST)

## What this system is
Live mainnet crypto trading stack. Running processes are REVENUE-CRITICAL.

## Hard rules
- Restarting trading units (`bitana-live-burst-follow`, `bitana-v5-paper`, PM2/systemd trading units): allowed for Claude Code since 2026-09-25 (owner grant), ONLY to deploy an owner-ordered change, and ONLY in a safe window: no open positions, no arm armed. Verify health after (process active, no errors in the log, bot not paused, websocket connected). Never stop or kill them otherwise.
- NEVER edit `config/*.yaml`, `config/loader.py`, or any file the running bots load, unless the owner (Martin) explicitly orders the exact change.
- NEVER place, modify, or cancel exchange orders. No positions, no orders, no account calls with side effects.
- NEVER touch `storage/*.db` databases except read-only via a /tmp copy (the live writer holds them).
- This is a two-agent workspace: Hermes (OWA) is the orchestrator. Propose patches; the OWNER applies prod changes himself unless he explicitly delegates.

## Useful facts
- Active live config: `config/live_burst_ny_asia.yaml` (running process: `main.py --config config/live_burst_ny_asia.yaml`). Root-level copies of configs are dead weight.
- Shadow research DB: `storage/signal_shadow.db` (~1.5GB, WAL, actively written by v5 paper harness) — read via a /tmp copy only.
- Live DB: `data/bitana-live-burst.db`. `trades` has NO entry_time (timestamp = exit; entry = timestamp − hold_time_s).
- Python: use `/root/bitana/venv/bin/python`. Scrub `API_FOOTBALL_KEY` from env before importing the config loader.
- Research plan (frozen prereg contract): `research/RESEARCH_PLAN.md` — never amend without owner order.

## Style
- Numbers first, no spin on loss reports. Telegram-bullet concision when reporting.