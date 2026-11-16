---
trigger: model_decision
description: "Guidelines and invariants for authoring or modifying Skyvern browser automation workflows."
---

# Skyvern Automation Rules

1. **Selector Priority**:
   - If an element has a reliable ID or data-test attribute, use hybrid actions: `page.click("#id", prompt="Description")`.
   - Use pure `page.act("Natural language")` only when layout variability demands dynamic discovery.
2. **State Assertions**:
   - Prefer `await page.validate("Intent verification")` over static sleep timeouts.
3. **Resource Cleanup**:
   - Always ensure browser contexts and working pages are closed in `finally` blocks or context managers.