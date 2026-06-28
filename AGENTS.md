# Agent Development Standards

## Quality Requirements

All changes to this project must adhere to the following standards:

### 1. Testing

- **All changes must pass the test suite**: Run `poetry run python -m pytest` before committing
- **New features must include new tests**: Any new functionality requires accompanying test coverage
- Tests must verify both happy path and edge cases
- Test files live in `tests/` and should match the module they test (e.g., `test_supplier_updates.py` for `supplier_updates.py`)

### 2. Pre-commit Hooks

- **All changes must pass pre-commit hooks**: Run `pre-commit run --all-files` before committing

### 3. Implementation Guidelines

- Extract helper functions to reduce complexity (prefer composition over monolithic functions)
- Use dataclasses to bundle related parameters and reduce function argument counts
- Document why suppressions are necessary if violations cannot be eliminated through refactoring
- Changes should not introduce new `# noqa` comments without addressing the underlying issue
- If changing a feature does _not_ introduce an expected test failure, investigate why the current tests do not fully exercise expected behaviour

## Workflow

1. **Write or update tests** for new or changed functionality, and run them to make sure they fail
2. **Make changes** to implement a feature or fix
3. **Run tests** to make sure feature has been implemented correctly
4. **Run linters**: `pre-commit run --all-files`
5. **Run tests**: `poetry run python -m pytest`
6. **Commit** only when both checks pass

## Example Commands

```bash
# Run all tests
poetry run python -m pytest

# Run specific test file
poetry run python -m pytest tests/test_supplier_updates.py -v

# Run all pre-commit hooks
pre-commit run --all-files

# Run just linting
pre-commit run ruff --all-files

# Run just formatting
pre-commit run ruff-format --all-files
```
