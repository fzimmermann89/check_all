# Check __init__.py __all__

This is a script and pre-commit hook to check and update the `__all__` variable in `__init__.py` files.

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

Now, on each commit, the hook will automatically check your `__init__.py` files.

## Arguments

- --line-length (default 120)
    set maximum line length
- --double-quotes
    use double quotes instead of single quotes

## Compatibility with ruff

The output of `--fix` is formatted to be stable under `ruff format`: single quotes
by default (use `--double-quotes` to change), a magic trailing comma keeps
multi-line `__all__` lists one-entry-per-line, and lines are wrapped at 120
characters (configurable via `--line-length`). Symbols are sorted isort-style,
matching ruff's `RUF022` rule (constants, then classes, then other names, with
natural sorting of digits). To avoid rewrite conflicts, place this hook before
the ruff hooks in your `.pre-commit-config.yaml`.
