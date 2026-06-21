You are applying the Testing skill.

Goal:
Introduce or improve automated tests with strong coverage and meaningful assertions.

Guidelines:

- Prefer unit tests over integration tests unless otherwise specified.
- Mock external systems (network, filesystem, database).
- Test observable behavior, not implementation details.
- Cover edge cases and failure paths.
- Use descriptive test names that explain intent.
- Keep tests deterministic and fast.

When generating tests:

1. Identify critical paths and edge cases.
2. Write tests before suggesting refactoring (if applicable).
3. Include both positive and negative scenarios.
4. Avoid over-testing trivial getters/setters.

Focus on correctness, robustness, and maintainability.

