import ast
import io
import re
import tokenize
from pathlib import Path
import argparse

NOQA_ALL_PATTERN = re.compile(r"# noqa: ALL(\[([a-zA-Z0-9_, ]+)\])?")


def _isort_key(name: str) -> tuple[int, tuple[tuple[int | str, ...], ...]]:
    """Sort key reproducing ruff's RUF022 "isort-style" ordering.

    Category first (SCREAMING_CASE constants, then CamelCase classes, then everything else),
    then a natural sort within the category (digit runs compared numerically).
    """
    if len(name.encode('utf-8')) > 1 and name.isupper():
        category = 0
    elif name[:1].isupper():
        category = 1
    else:
        category = 2
    natural = []
    for part in re.findall(r"[0-9]+|[^0-9]", name):
        if part[0] in "123456789":
            natural.append((ord('1'), len(part), part))
        else:
            # Ruff compares digit runs starting with zero lexicographically.
            natural.append((ord(part[0]), part))
    return category, tuple(natural)


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
    with filepath.open("r") as file:
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
    imports = {name.split(".")[-1] for name in imports}

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
        return set(map(str.strip, match.group(2).split(","))) if match.group(2) else "ALL"
    return None


def format_all_string(symbols: list[str], line_length: int, use_double_quotes: bool, multiline: bool) -> str:
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
    multiline
        If True, always use one entry per line with a trailing comma (like ruff
        format's magic trailing comma). If False, use a single line without a
        trailing comma if it fits within line_length.

    Returns
    -------
    A formatted string representing the __all__ list.
    """
    if not symbols:
        return "__all__ = []"
    quote_char = '"' if use_double_quotes else "'"
    all_decl = "__all__ = ["
    all_items = [f"{quote_char}{symbol}{quote_char}" for symbol in symbols]
    single_line = f"{all_decl}{', '.join(all_items)}]"
    if not multiline and len(single_line) <= line_length:
        return single_line

    formatted_symbols = ",\n    ".join(all_items)
    return f"__all__ = [\n    {formatted_symbols},\n]"


def get_all_comments(source: str, node: ast.AST) -> list[tokenize.TokenInfo]:
    """Collect comments belonging to the declaration, excluding later statements."""
    lines = source.splitlines()
    comments = []
    for token in tokenize.generate_tokens(io.StringIO(source).readline):
        if token.type != tokenize.COMMENT or not node.lineno <= token.start[0] <= node.end_lineno:
            continue
        if token.start[0] == node.end_lineno:
            end_column = len(lines[node.end_lineno - 1].encode('utf-8')[:node.end_col_offset].decode('utf-8'))
            if lines[node.end_lineno - 1][end_column:token.start[1]].strip(' ;\t'):
                continue
        comments.append(token)
    return comments


def preserve_all_comments(
    all_string: str, symbols: list[str], node: ast.Assign, comments: list[tokenize.TokenInfo]
) -> str:
    """Keep boundary comments and move element comments with their symbols."""
    opening, closing, orphaned = [], [], []
    before, after = {}, {}
    for comment in comments:
        lineno = comment.start[0]
        previous = [elt for elt in node.value.elts if elt.lineno <= lineno <= elt.end_lineno]
        if lineno == node.end_lineno and (lineno != node.lineno or not parse_noqa(comment.string)):
            closing.append(comment.string)
        elif lineno == node.lineno and (not previous or parse_noqa(comment.string)):
            opening.append(comment.string)
        elif previous:
            symbol = previous[-1].value
            if symbol in symbols:
                after.setdefault(symbol, []).append(comment.string)
            else:
                orphaned.append(comment.string)
        else:
            following = next((elt for elt in node.value.elts if elt.lineno > lineno), None)
            if following is not None and following.value in symbols:
                before.setdefault(following.value, []).append(comment.string)
            else:
                orphaned.append(comment.string)

    lines = all_string.splitlines()
    if closing and node.lineno != node.end_lineno and len(lines) == 1:
        lines = ["__all__ = [", "]"]
    if before or after or orphaned:
        if not symbols:
            lines = ["__all__ = [", "]"]
        decorated = [lines[0]]
        for symbol, line in zip(symbols, lines[1:-1]):
            decorated.extend('    ' + comment for comment in before.get(symbol, []))
            inline = after.get(symbol, [])
            decorated.extend('    ' + comment for comment in inline[1:])
            decorated.append(line + ('  ' + inline[0] if inline else ''))
        decorated.extend('    ' + comment for comment in orphaned)
        decorated.append(lines[-1])
        lines = decorated
    if opening:
        lines[0] += '  ' + ' '.join(opening)
    if closing:
        lines[-1] += '  ' + ' '.join(closing)
    return '\n'.join(lines)


def update_all_in_init(
    filepath: Path, line_length: int = 120, use_double_quotes: bool = True, fix: bool = True
) -> None:
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
    with filepath.open("r") as file:
        source = file.read()

    tree = ast.parse(source, filename=str(filepath))
    imports = get_all_imports(filepath)

    all_var, all_lineno, noqa_symbols, errors = None, None, set(), []
    all_start, all_end = None, None

    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "__all__" for t in node.targets):
            all_var = [elt.value if isinstance(elt, ast.Constant) else elt.s for elt in node.value.elts]
            all_lineno = node.lineno
            all_start = node.lineno - 1
            all_end = node.end_lineno - 1 if hasattr(node, "end_lineno") else all_start
            comments = get_all_comments(source, node)
            for comment in comments:
                if comment.start[0] == node.lineno:
                    parsed_noqa = parse_noqa(comment.string)
                    if parsed_noqa == "ALL":
                        return
                    if parsed_noqa:
                        noqa_symbols.update(parsed_noqa)
            break

    if all_var is None:
        new_all = sorted(imports, key=_isort_key)
        all_string = format_all_string(new_all, line_length, use_double_quotes, multiline=False)
        if fix:
            with filepath.open("w") as file:
                file.write(source.rstrip() + "\n\n" + all_string + "\n")
        print(f"{filepath}\033[31m error:\033[0m missing __all__")
        return

    missing = sorted(imports - set(all_var) - noqa_symbols)
    extra = sorted(set(all_var) - imports - noqa_symbols)
    is_sorted = all_var == sorted(all_var, key=_isort_key)
    print_errors(filepath, all_lineno, is_sorted, missing, extra)
    updated_all = sorted((set(all_var) | set(missing)) - set(extra), key=_isort_key)

    multiline = all_end > all_start
    all_string = format_all_string(updated_all, line_length, use_double_quotes, multiline)

    if fix and (not is_sorted or missing or extra):
        lines = source.splitlines()
        if comments:
            all_string = preserve_all_comments(all_string, updated_all, node, comments)
        prefix = lines[all_start].encode('utf-8')[:node.col_offset].decode('utf-8')
        suffix = lines[all_end].encode('utf-8')[node.end_col_offset:].decode('utf-8')
        if any(comment.start[0] == node.end_lineno for comment in comments):
            suffix = ''
        lines[all_start : all_end + 1] = [prefix + all_string + suffix]
        # normalize to exactly one blank line before __all__ (like ruff format)
        pre = all_start
        while pre > 0 and lines[pre - 1].strip() == "":
            pre -= 1
        lines[pre:all_start] = [""] if pre > 0 else []
        with filepath.open("w") as file:
            file.write("\n".join(lines) + "\n")


def print_errors(filepath: Path, all_lineno: int, is_sorted: bool, missing: set, extra: set) -> None:
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
        errors.append("Error: __all__ is not sorted (isort-style, like ruff RUF022).")

    if missing:
        errors.append(f"Error: Missing symbols in __all__: {missing}")

    if extra:
        errors.append(f"Error: Extra symbols in __all__: {extra}")

    for error in errors:
        print(f"{filepath}:{all_lineno}:\033[31m error:\033[0m {error}")

    if errors:
        ignored_symbols = sorted(set(missing + extra))
        noqa_suggestion = f"ALL[{','.join(ignored_symbols)}]" if ignored_symbols else "ALL"
        print(
            f"{filepath}:{all_lineno}:\033[34m note\033[0m: You can silence this  error by using `# noqa: {noqa_suggestion}` "
        )


def check_all_in_paths(paths: list[Path], line_length: int, use_double_quotes: bool, fix: bool) -> None:
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
        if path.is_file() and path.name == "__init__.py":
            update_all_in_init(path, line_length, use_double_quotes, fix)
        elif path.is_dir():
            for init_file in path.rglob("__init__.py"):
                update_all_in_init(init_file, line_length, use_double_quotes, fix)
        else:
            print(f"Warning: {path} is not a valid file or directory.")


def main():
    parser = argparse.ArgumentParser(description="Check and update __all__ in __init__.py files.")
    parser.add_argument(
        "paths",
        nargs="*",
        default=["."],
        help="Paths to the directories or __init__.py files to check (default: current directory)",
    )
    parser.add_argument(
        "--line-length",
        "-l",
        type=int,
        default=120,
        help="Maximum line length for __all__",
    )
    parser.add_argument(
        "--double-quotes",
        action="store_true",
        help="Use double quotes for __all__ instead of single quotes",
    )
    parser.add_argument(
        "--fix",
        "-f",
        action="store_true",
        help="apply fixes",
    )
    args = parser.parse_args()

    check_all_in_paths([Path(p) for p in args.paths], args.line_length, args.double_quotes, args.fix)


if __name__ == "__main__":
    main()
