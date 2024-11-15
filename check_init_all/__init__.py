import ast
import re
from pathlib import Path
import argparse

NOQA_ALL_PATTERN = re.compile(r"# noqa: ALL(\[([a-zA-Z0-9_, ]+)\])?")

def get_all_imports(filepath: Path) -> set[str]:
    """
    Parse a Python file to extract all symbols from import statements that should be included in __all__.

    Parameters
    ----------
    filepath
        The path to the Python file to parse.

    Returns
    -------
    A set of unique import symbols to be included in __all__.
    """
    with filepath.open('r') as file:
        tree = ast.parse(file.read(), filename=str(filepath))

    imports = set()

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            # For standard imports like import x
            for alias in node.names:
                imports.add(alias.asname or alias.name)
        elif isinstance(node, ast.ImportFrom):
            # For from x import y statements
            if node.level == 0 and node.module:  # Normal imports like from x import y
                for alias in node.names:
                    # Add only the name being imported, not the full module path
                    imports.add(alias.asname or alias.name)
            elif node.level > 0:  # Relative imports, like from .module import x
                for alias in node.names:
                    # Same handling for relative imports
                    imports.add(alias.asname or alias.name)

    # Remove any full module paths (if they exist in the imports)
    imports = {name.split('.')[-1] for name in imports}

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

def format_all_string(symbols: list[str], line_length: int, use_double_quotes: bool) -> str:
    """
    Format the __all__ string based on the provided line length and quote style.

    Parameters
    ----------
    symbols
        A list of symbols to be included in __all__.
    line_length
        The maximum length of a line.
    use_double_quotes
        If True, use double quotes ("), else use single quotes (').

    Returns
    -------
    A formatted string representing the __all__ list.
    """
    quote_char = '"' if use_double_quotes else "'"
    all_decl = "__all__ = ["
    all_items = [f"{quote_char}{symbol}{quote_char}" for symbol in symbols]    
    if len(all_decl) + len(', '.join(all_items)) + 2 <= line_length:
        return f"{all_decl}{', '.join(all_items)}]"

    formatted_symbols = ",\n    ".join(all_items)
    return f"__all__ = [\n    {formatted_symbols}\n]"


def update_all_in_init(filepath: Path, line_length: int = 79, use_double_quotes: bool = True, fix:bool=True) -> None:
    """
    Validate and update the __all__ variable in a given __init__.py file.

    Checks if all imports are listed in __all__, ensures __all__ is sorted, 
    and creates __all__ if it does not exist. Provides error messages for 
    missing or extra symbols and writes updates back to the file.

    Parameters
    ----------
    filepath
        The path to the __init__.py file to validate and update.
    line_length
        The maximum allowed length of a line before wrapping the __all__ string.
    use_double_quotes
        If True, use double quotes for the __all__ string, otherwise use single quotes.
    fix
        If True, apply fixes
    """
    with filepath.open('r') as file:
        source = file.read()

    tree = ast.parse(source, filename=str(filepath))
    imports = get_all_imports(filepath)

    all_var, all_lineno, noqa_symbols, errors = None, None, set(), []
    all_start, all_end = None, None

    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "__all__" for t in node.targets):
            all_var = [elt.s for elt in node.value.elts]
            all_lineno = node.lineno
            all_start = node.lineno - 1 
            all_end = node.end_lineno - 1 if hasattr(node, "end_lineno") else all_start
            source_lines = source.splitlines()
            parsed_noqa = parse_noqa(source_lines[all_lineno - 1])
            if parsed_noqa == "ALL":
                return
            noqa_symbols = parsed_noqa if parsed_noqa else set()
            break

    if all_var is None:
        new_all = sorted(imports)
        with filepath.open('a') as file:
            file.write(f"\n{format_all_string(new_all, line_length, use_double_quotes)}\n")
        print(f"{filepath}\033[31m error:\033[0m missing __all__") 
        return

    missing = sorted(imports - set(all_var) - noqa_symbols)
    extra = sorted(set(all_var) - imports - noqa_symbols)
    is_sorted = all_var == sorted(all_var)
    print_errors(filepath, all_lineno, is_sorted, missing, extra)
    updated_all = sorted((set(all_var) | set(missing)) - set(extra))

    all_string = format_all_string(updated_all, line_length, use_double_quotes)

    if fix and (not is_sorted or missing or extra):
        lines = source.splitlines()
        lines[all_start:all_end + 1] = [all_string]
        with filepath.open('w') as file:
            file.write("\n".join(lines))

def print_errors(filepath: Path, all_lineno: int, is_sorted:bool, missing:set, extra:set) -> None:
    """
    Print the errors and notes in the format similar to MyPy.

    Parameters
    ----------
    filepath
        The path to the __init__.py file being processed.
    all_lineno
        The line number where __all__ is defined.
    all_var
        The current set of symbols in __all__.
    imports
        The set of imports found in the file.
    """
    errors = []


    if not is_sorted:
        errors.append("Error: __all__ is not sorted alphabetically.")

    if missing:
        errors.append(f"Error: Missing symbols in __all__: {missing}")

    if extra:
        errors.append(f"Error: Extra symbols in __all__: {extra}")

    for error in errors:
        print(f"{filepath}:{all_lineno}:\033[31m error:\033[0m {error}")  

    if errors:
        ignored_symbols = sorted(set(missing + extra))
        noqa_suggestion = f"ALL[{','.join(ignored_symbols)}]" if ignored_symbols else "ALL"
        print(f"{filepath}:{all_lineno}:\033[34m note\033[0m: You can silence this  error by using `# noqa: {noqa_suggestion}` ")

def check_all_in_paths(paths: list[Path], line_length: int, use_double_quotes: bool, fix:bool) -> None:
    """
    Process multiple paths to check and update __init__.py files.

    If a path is a directory, it will recursively search for __init__.py files.
    If a path is a file and is __init__.py, it will be processed directly.

    Parameters
    ----------
    paths
        A list of file or directory paths to check.
    line_length
        The maximum length of a line in the generated __all__.
    use_double_quotes
        Whether to use double quotes for __all__ or not.
    fix
        Wether to apply fixes if possible or not
    """
    for path in paths:
        if path.is_file() and path.name == '__init__.py':
            update_all_in_init(path, line_length, use_double_quotes, fix)
        elif path.is_dir():
            for init_file in path.rglob('__init__.py'):
                update_all_in_init(init_file, line_length, use_double_quotes, fix)
        else:
            print(f"Warning: {path} is not a valid file or directory.")

def main():
    parser = argparse.ArgumentParser(description='Check and update __all__ in __init__.py files.')
    parser.add_argument('paths', nargs='*', default=['.'], help='Paths to the directories or __init__.py files to check (default: current directory)')
    parser.add_argument('--line-length', '-l', type=int, default=80, help='Maximum line length for __all__')
    parser.add_argument('--double-quotes', action='store_true', help='Use double quotes for __all__ instead of single quotes')
    parser.add_argument('--fix', '-f', action='store_true', help='apply fixes')
    args = parser.parse_args()

    check_all_in_paths([Path(p) for p in args.paths], args.line_length, args.double_quotes, args.fix)

if __name__ == "__main__":
    main()