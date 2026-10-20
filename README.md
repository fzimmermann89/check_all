# Check __init__.py __all__

Check and update `__all__` in `__init__.py` files with a script or pre-commit hook.

## Installation in pre-commit

1. Install pre-commit:

   ```bash
   pip install pre-commit
   ```

2. Install the pre-commit hooks:

   ```bash
   pre-commit install
   ```

3. Add the hook to your `.pre-commit-config.yaml` file:

   ```yaml
   repos:
     - repo: https://github.com/fzimmermann89/check_all
       rev: master
       hooks:
         - id: check-init-all
   ```

## Arguments

- `--line-length N`: maximum line length (default 120).
- `--double-quotes`: use double quotes instead of single quotes.

## Ruff

Place this hook before Ruff. Sorting follows `RUF022`; multiline lists use
trailing commas. Pass `--double-quotes` to match Ruff's default, or configure
Ruff with `quote-style = "single"`.
