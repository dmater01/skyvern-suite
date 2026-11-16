# Antigravity Autonomous Agent Workspace Guidelines

## Project Identity & Architecture
This workspace implements a dynamic, self-configuring web automation engine built with:
- **Skyvern** (Playwright + Vision LLM orchestration)
- **FastAPI** (Async backend service for API-driven automation tasks)
- **Streamlit** (Interactive frontend for real-time URL introspection and dynamic form generation)
- **Docker Compose** (Production deployment containerizing the API, workers, and UI)

## Development & Production Rules
1. **Zero Secret Leaks**: Never hardcode API keys or credentials. Use `.env` or vault integrations (Bitwarden/1Password).
2. **Deterministic Schemas**: All data extraction actions (`page.extract` / `skyvern.run_task`) MUST specify strict JSON schemas with required attributes.
3. **Fault Tolerance**: Wrap all multi-step iterations in granular retry blocks with exponential backoff and visual state recovery (`recover_page_state`).
4. **Session Integrity**: In long-running batch tasks, always verify session state with `page.validate()` before navigating.
5. **Testing Requirements**: Any added feature or refactoring must include integration tests runnable with `pytest`.