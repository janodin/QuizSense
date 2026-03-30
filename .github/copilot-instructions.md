# GitHub Copilot Instructions for QuizSense

## Virtual Environment

- **Always activate the virtual environment** before running any Python command.
- The venv is located at `venv/` in the project root.
- Activation command (Windows PowerShell):
  ```powershell
  .\venv\Scripts\Activate.ps1
  ```
- Never run `python`, `pip`, `django-admin`, or `manage.py` commands without the venv being active.
- If a new terminal is opened, activate the venv first before proceeding.

## TASKS.md Tracking

- **Always check `TASKS.md`** before starting any work to understand what has been done and what is pending.
- Before working on a task, confirm it is not already checked off.
- **Immediately after completing a task**, update `TASKS.md` by marking the corresponding checkbox as done:
  - Not done: `- [ ]`
  - Done: `- [x]`
- Do not batch-complete tasks. Mark each task as done the moment it is finished.
- If a new task arises that is not in `TASKS.md`, add it to the appropriate phase before starting.

## General Rules

- Always follow the phase order defined in `TASKS.md` unless there is a clear dependency reason not to.
- The project uses **Django** (backend), **Django Templates** (frontend), **PostgreSQL** (database), **Bootstrap** (styling), and **Gemini API** (AI quiz generation).
- Use **function-based views** only in `views.py` — no class-based views.
- Keep all secrets (API keys, DB credentials) in a `.env` file and never hardcode them.
