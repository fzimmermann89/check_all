import ast
import re
from pathlib import Path
import argparse

NOQA_ALL_PATTERN = re.compile(r"# noqa: ALL(\[([a-zA-Z0-9_, ]+)\])?")

def get_all_imports(filepath: Path) -> set[str]:
    """
    Parse a Python file to extract all import statements.

    Parameters
    ----------
    filepath
        The path to the Python file to parse.

    Returns
    -------
    A set of unique import names found in the file.
    """
    with filepath.open('r') as file:
        tree = ast.parse(file.read(), filename=str(filepath))

    imports = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module)

    return imports

def parse_noqa(comment: str) -> set[str] | str | None:
    """
    Extract symbols to ignore from a # noqa: ALL comment.

    Parameters
    ----------
    comment
        The comment line containing the noqa directive.

    Returns
    -------
    A set of ignored symbols if present, 
    "ALL" to ignore all checks, or None.
    """
    match = NOQA_ALL_PATTERN.search(comment)
    if match:
        return set(map(str.strip, match.group(2).split(','))) if match.group(2) else "ALL"
    return None

def update_all_in_init(filepath: Path) -> None:
    """
    Validate and update the __all__ variable in a given __init__.py file.

    Checks if all imports are listed in __all__, ensures __all__ is sorted, 
    and creates __all__ if it does not exist. Provides error messages for 
    missing or extra symbols and writes updates back to the file.

    Parameters
    ----------
    filepath
        The path to the __init__.py file to validate and update.
    """
    with filepath.open('r') as file:
        source = file.read()

    tree = ast.parse(source, filename=str(filepath))
    imports = get_all_imports(filepath)

    all_var, all_lineno, noqa_symbols, errors = None, None, set(), []

    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "__all__" for t in node.targets):
            all_var = {elt.s for elt in node.value.elts}
            all_lineno = node.lineno
            source_lines = source.splitlines()
            parsed_noqa = parse_noqa(source_lines[all_lineno - 1])
            if parsed_noqa == "ALL":
                return
            noqa_symbols = parsed_noqa if parsed_noqa else set()
            break

    if all_var is None:
        new_all = sorted(imports)
        with filepath.open('a') as file:
            file.write(f"\n__all__ = {new_all}\n")
        print(f"Creating __all__ in {filepath}")
        return

    missing = sorted(imports - all_var - noqa_symbols)
    extra = sorted(all_var - imports - noqa_symbols)
    sorted_all_var = sorted(all_var)

    if list(all_var) != sorted_all_var:
        errors.append("Error: __all__ is not sorted alphabetically.")

    if missing:
        errors.append(f"Error: Missing symbols in __all__: {missing}")

    if extra:
        errors.append(f"Error: Extra symbols in __all__: {extra}")

    if errors:
        print(f"Errors found in {filepath}:")
        for error in errors:
            print(error)

        ignored_symbols = sorted(set(missing + extra))
        noqa_suggestion = f"ALL[{','.join(ignored_symbols)}]" if ignored_symbols else "ALL"
        print(f"\nYou can silence specific errors by using `# noqa: {noqa_suggestion}` "
              "on the __all__ line, or `# noqa: ALL` to ignore the entire __all__ validation.\n")

    updated_all = sorted(all_var.union(missing) - set(extra))

    if updated_all != sorted_all_var or missing or extra:
        updated_all_line = f"__all__ = {updated_all}"
        lines = source.splitlines()
        lines[all_lineno - 1] = updated_all_line
        with filepath.open('w') as file:
            file.write("\n".join(lines))
        print(f"Updated __all__ in {filepath}")

def check_all_in_paths(paths: list[Path]) -> None:
    """
    Process multiple paths to check and update __init__.py files.

    If a path is a directory, it will recursively search for __init__.py files.
    If a path is a file and is __init__.py, it will be processed directly.

    Parameters
    ----------
    paths
        A list of file or directory paths to check.
    """
    for path in paths:
        if path.is_file() and path.name == '__init__.py':
            update_all_in_init(path)
        elif path.is_dir():
            for init_file in path.rglob('__init__.py'):
                update_all_in_init(init_file)
        else:
            print(f"Warning: {path} is not a valid file or directory.")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Check and update __all__ in __init__.py files.')
    parser.add_argument('paths', nargs='*', default=['.'], help='Paths to the directories or __init__.py files to check (default: current directory)')
    args = parser.parse_args()
    check_all_in_paths([Path(p) for p in args.paths])
