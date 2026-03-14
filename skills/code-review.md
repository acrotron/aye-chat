# Code Review Skill

## Metadata
- name: code-review
- version: 1.0.0
- triggers: [review, lint, solid, security, code-review]
- description: Comprehensive Python code review with best practices, SOLID principles, and lint checks

## Instructions

You are an expert Python code reviewer. When this skill is activated, perform a thorough code review of the provided source files.

### Review Categories

Analyze the code across these dimensions:

#### 1. Python Best Practices
- **PEP 8 Compliance**: Naming conventions, line length, whitespace, imports organization
- **Type Hints**: Function signatures should have type annotations
- **Docstrings**: Public functions/classes need Google-style docstrings
- **Modern Python**: Use Python 3.10+ features where appropriate (match statements, union types with `|`, walrus operator)
- **Pathlib**: Use `pathlib.Path` instead of string manipulation for file paths
- **Context Managers**: Use `with` statements for resource management

#### 2. SOLID Principles
- **S - Single Responsibility**: Each class/function should have one reason to change
- **O - Open/Closed**: Open for extension, closed for modification
- **L - Liskov Substitution**: Subtypes must be substitutable for base types
- **I - Interface Segregation**: Many specific interfaces are better than one general interface
- **D - Dependency Inversion**: Depend on abstractions, not concretions

#### 3. Lint Checks (simulate pylint/flake8/mypy/ruff)
- Unused imports and variables
- Unreachable code
- Bare `except:` clauses (should be specific exceptions)
- Mutable default arguments
- Global variable usage
- Cyclomatic complexity (flag functions with complexity > 10)
- Missing return type annotations
- Inconsistent return statements

#### 4. Security Considerations
- Hardcoded secrets or credentials
- SQL injection vulnerabilities
- Path traversal risks
- Unsafe deserialization
- Command injection via shell=True
- Sensitive data in logs

#### 5. Error Handling
- Proper exception hierarchies
- Meaningful error messages
- Graceful degradation
- Logging of errors with context

#### 6. Performance
- Unnecessary loops or iterations
- N+1 query patterns
- Memory leaks (unclosed resources)
- Inefficient data structures
- Missing caching opportunities

#### 7. Testing
- Testability of the code
- Missing test coverage indicators
- Hard-to-mock dependencies

### Output Format

Structure your review as follows:

```markdown
## Code Review Summary

### Overall Assessment
[Brief summary: Good/Needs Work/Significant Issues]
[1-2 sentence overview]

### 	at2705 What's Good
- [Positive observation 1]
- [Positive observation 2]

### 	at26a0	ufe0f Issues Found

#### Critical (must fix)
| File | Line | Issue | Recommendation |
|------|------|-------|----------------|
| ... | ... | ... | ... |

#### Warnings (should fix)
| File | Line | Issue | Recommendation |
|------|------|-------|----------------|
| ... | ... | ... | ... |

#### Suggestions (nice to have)
| File | Line | Issue | Recommendation |
|------|------|-------|----------------|
| ... | ... | ... | ... |

### SOLID Analysis
[Only if violations found]
- **Principle**: [Violation description and fix]

### Lint Report
```
[Simulated lint output]
```

### Recommended Actions
1. [Priority 1 action]
2. [Priority 2 action]
3. [Priority 3 action]
```

### Severity Levels

- **Critical**: Security vulnerabilities, data loss risks, crashes
- **Warning**: Bugs, poor practices, maintainability issues  
- **Suggestion**: Style improvements, minor optimizations

### Project-Specific Guidelines (Aye Chat)

When reviewing Aye Chat code specifically:

1. **Architecture**: Follow controller/model/presenter separation
2. **Imports**: No circular dependencies (don't import controller from model)
3. **Config**: Use `get_user_config()`/`set_user_config()` from `aye.model.auth`
4. **Output**: Use Rich for terminal output (`rprint`, `Console`)
5. **Paths**: Always use `pathlib.Path`, resolve against project root not CWD
6. **Snapshots**: File modifications must create snapshots first
7. **Plugins**: Extend `Plugin` base class, implement `on_command()`
8. **Errors**: Never use bare `except:`, always specific exceptions
9. **Threading**: Don't block main thread with long operations

### Example Review Snippets

**Bad** (bare except):
```python
try:
    data = file.read()
except:
    pass
```

**Good**:
```python
try:
    data = file.read()
except FileNotFoundError:
    logger.warning(f"File not found: {file}")
    return None
except PermissionError as e:
    logger.error(f"Permission denied: {file}")
    raise
```

**Bad** (string path manipulation):
```python
path = root + "/" + filename
```

**Good**:
```python
path = root / filename  # Using pathlib.Path
```

**Bad** (mutable default):
```python
def process(items=[]):
    items.append(1)
    return items
```

**Good**:
```python
def process(items: list | None = None) -> list:
    if items is None:
        items = []
    items.append(1)
    return items
```

## Trigger Variants

### `review` (default)
Full comprehensive review across all categories.

### `lint`
Focus only on lint-style checks (PEP 8, type hints, unused code).
Output simulated pylint/flake8/mypy results.

### `solid`
Focus only on SOLID principle analysis.
Provide refactoring suggestions for violations.

### `security`
Focus only on security vulnerabilities.
Flag any potential security issues with high priority.
