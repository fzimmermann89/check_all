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
     - repo: https://github.com/yourusername/your-repo
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
