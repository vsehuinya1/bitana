# Research Duo Collaboration Contract

This contract defines the protocols, roles, and boundaries for the collaborative research effort on the Bitana trading bot system between the Human Lead and the AI Research Agent Duo (Antigravity & Cursor).

## 1. Objectives & Scope
The primary focus of the Research Duo is to:
- Perform forensic analysis of the live trading engine vs. backtesting behavior.
- Validate telemetry and execution anomalies (e.g., exit times, trade frequency, cascade detection).
- Develop and safely verify optimizations before proposing integration into the production codebase.

## 2. Roles & Agent Boundaries
- **Human Lead (Martin):** Final authority on code promotion, live deployment, API key access, and strategic direction.
- **Antigravity (System Architect Agent):** Responsible for structural consistency, verification, and code quality control.
- **Cursor (Feature & Implementation Agent):** Focuses on local feature updates, debugging, and iteration.

## 3. Ground Rules & Safety
- **No Unapproved Live Code Execution:** No agent shall run arbitrary code in a production environment or access external networks without explicit confirmation.
- **Sequential Executions:** To maintain clear history and avoid conflict, do not run parallel/concurrent agents editing the same workspace.
- **Verification First:** All code modifications must be compiled and/or test-verified locally (e.g., using `py_compile` or unit tests) before marking as completed.

## 4. Work Tracking & Hand-offs
- Progress and task management will be recorded under the `.gemini/antigravity/brain/` workspace artifacts.
- Key findings must be updated in `current_system_state.md` and handed off cleanly between sessions.
