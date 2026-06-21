You are applying the Modularization skill.

Goal:
Refactor the codebase into well-defined, cohesive modules with clear responsibilities and minimal coupling.

Guidelines:

- Identify logical boundaries (domain, infrastructure, interfaces, utilities, etc.).
- Separate concerns aggressively.
- Eliminate circular dependencies.
- Prefer small, focused modules over large multi-purpose files.
- Extract reusable abstractions where duplication appears.
- Introduce interfaces where boundaries are crossed.
- Keep public APIs minimal and explicit.
- Do not change behavior unless explicitly instructed.

When refactoring:

1. First propose the target module structure.
2. Explain why each boundary exists.
3. Show incremental changes.
4. Preserve tests or suggest new ones where necessary.

Prioritize clarity and long-term maintainability over cleverness.

