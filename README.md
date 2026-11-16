# Skyvern Dynamic App Suite (Antigravity-Ready)

This repository is optimized for autonomous development and continuous iteration using **Google Antigravity CLI** (`agy`).

## Repository Architecture
- **`.antigravity/` & `.agents/`**: Antigravity agent configuration, MCP server references, and workspace rules.
- **`AGENTS.md` / `GEMINI.md`**: Behavioral constraints, coding standards, and verification guidelines for Antigravity agents.
- **`src/api/`**: FastAPI backend service for headless automation triggers.
- **`src/frontend/`**: Streamlit dynamic UI generator driven by page introspection.
- **`src/core/`**: Core perception and execution engine.
- **`workflows/`**: Standalone batch runners and screening scripts.
- **`tests/`**: Pytest suite for schema validation and workflow logic.

## Developing with Google Antigravity CLI

1. **Launch Antigravity Session**:
   ```bash
   agy
   ```
2. **Interact with the Agent**:
   ```text
   /status
   Explain the dynamic app introspection pipeline and run unit tests.
   ```
3. **Execute in Plan Mode**:
   Press `Shift + Tab` in `agy` to enter `plan` mode before proposing multi-file refactors.