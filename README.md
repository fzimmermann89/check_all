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
- `--double-quotes`: use double quotes (default).
- `--single-quote` (alias `--single-quotes`): use single quotes.
- `--fix`: rewrite incorrect declarations.

Without `--fix`, files stay unchanged. Exit status is 1 if issues are found
(including repaired issues), otherwise 0.

`__all__` must be a literal list of strings; `--fix` replaces other forms.

## Ruff

Place this hook before Ruff. Sorting follows `RUF022`; multiline lists use
trailing commas. Use `--single-quote` if Ruff uses `quote-style = "single"`.
