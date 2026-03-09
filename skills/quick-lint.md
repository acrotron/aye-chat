# Quick Lint Skill

## Metadata
- name: quick-lint
- version: 1.0.0
- triggers: [qlint, quick-lint, lint-quick]
- description: Fast lint check without full review

## Instructions

Perform a quick lint-style check on the provided Python files. Output should be concise and actionable.

### Check For

1. **Imports**
   - Unused imports
   - Import order (stdlib, third-party, local)
   - Circular import risks

2. **Type Hints**
   - Missing function return types
   - Missing parameter types on public functions

3. **Code Style**
   - Line length > 100 characters
   - Trailing whitespace
   - Missing docstrings on public functions

4. **Common Bugs**
   - Bare `except:` clauses
   - Mutable default arguments
   - `==` vs `is` for None/True/False
   - f-strings without placeholders

5. **Unused Code**
   - Unused variables
   - Unreachable code after return/raise
   - Commented-out code blocks

### Output Format

```
	at1f50d Lint Check Results

file.py:
  L12: W001 Unused import 'os'
  L45: E001 Bare except clause
  L67: W002 Missing return type annotation
  L89: E002 Mutable default argument []

other_file.py:
  L5:  W001 Unused variable 'temp'
  L23: W003 Line too long (124 > 100)

---
Total: 2 errors, 4 warnings
```

### Error Codes

- **E0xx**: Errors (likely bugs)
- **W0xx**: Warnings (code smells)
- **C0xx**: Convention (style issues)
- **R0xx**: Refactor (complexity issues)
