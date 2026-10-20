import ast
import io
import re
import tokenize
from pathlib import Path
import argparse

NOQA_ALL_PATTERN = re.compile(r"# noqa: ALL(\[([a-zA-Z0-9_, ]+)\])?")


def module_nodes(node: ast.AST):
    """Walk module code without entering function or class scopes."""
    yield node
    for child in ast.iter_child_nodes(node):
        if not isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
            yield from module_nodes(child)


def is_all_statement(node: ast.AST) -> bool:
    """Identify declarations and direct modifications of module __all__."""
    if isinstance(node, (ast.Assign, ast.Delete)):
        targets = node.targets
    elif isinstance(node, (ast.AnnAssign, ast.AugAssign)):
        targets = [node.target]
    elif (isinstance(node, ast.Expr) and isinstance(node.value, ast.Call)
          and isinstance(node.value.func, ast.Attribute) and isinstance(node.value.func.value, ast.Name)
          and node.value.func.attr in {'append', 'extend', 'insert', 'remove', 'pop', 'clear', 'sort', 'reverse',
                                      '__setitem__', '__delitem__', '__iadd__', '__imul__'}):
        targets = [node.value.func.value]
    else:
        return False
    for target in targets:
        while isinstance(target, (ast.Subscript, ast.Attribute)):
            target = target.value
        if isinstance(target, ast.Name) and target.id == "__all__":
            return True
    return False


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

    for node in module_nodes(tree):
        if isinstance(node, ast.Import):
            # For standard imports like import x
            for alias in node.names:
                imports.add(alias.asname or alias.name.split('.')[0])
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
    matches = list(NOQA_ALL_PATTERN.finditer(comment))
    if matches:
        if any(not match.group(2) for match in matches):
            return "ALL"
        return {symbol.strip() for match in matches for symbol in match.group(2).split(',')}
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
    all_string: str, symbols: list[str], node: ast.AST, comments: list[tokenize.TokenInfo]
) -> str:
    """Keep boundary comments and move element comments with their symbols."""
    opening, closing, orphaned = [], [], []
    before, after = {}, {}
    elements = [elt for elt in getattr(getattr(node, 'value', None), 'elts', [])
                if isinstance(elt, ast.Constant) and isinstance(elt.value, str)]
    for comment in comments:
        lineno = comment.start[0]
        previous = [elt for elt in elements if elt.lineno <= lineno <= elt.end_lineno]
        if lineno == node.end_lineno:
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
            following = next((elt for elt in elements if elt.lineno > lineno), None)
            if following is not None and following.value in symbols:
                before.setdefault(following.value, []).append(comment.string)
            else:
                orphaned.append(comment.string)

    lines = all_string.splitlines()
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
) -> bool:
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

    all_nodes = [node for node in module_nodes(tree) if is_all_statement(node)]
    valid_declaration = (
        len(all_nodes) == 1 and all_nodes[0] in tree.body
        and isinstance(all_nodes[0], ast.Assign) and len(all_nodes[0].targets) == 1
        and isinstance(all_nodes[0].targets[0], ast.Name)
        and isinstance(all_nodes[0].value, ast.List)
        and all(isinstance(elt, ast.Constant) and isinstance(elt.value, str) for elt in all_nodes[0].value.elts)
    )
    if all_nodes and not valid_declaration:
        comments = [comment for node in all_nodes for comment in get_all_comments(source, node)]
        noqa_symbols, noqa_comments = set(), []
        for node in all_nodes:
            for comment in get_all_comments(source, node):
                if comment.start[0] in (node.lineno, node.end_lineno):
                    parsed_noqa = parse_noqa(comment.string)
                    if parsed_noqa == "ALL":
                        return False
                    if parsed_noqa:
                        noqa_symbols.update(parsed_noqa)
                        noqa_comments.append(comment)
        comments = [comment for comment in comments if comment not in noqa_comments]
        print(f"{filepath}:{all_nodes[0].lineno}: error: __all__ must be a single literal list of strings.")
        if fix:
            retained = {elt.value for node in all_nodes for elt in ast.walk(node)
                        if isinstance(elt, ast.Constant) and isinstance(elt.value, str)}
            new_all = sorted((imports - noqa_symbols) | (retained & noqa_symbols), key=_isort_key)
            all_string = format_all_string(new_all, line_length, use_double_quotes, multiline=bool(comments))
            all_string = preserve_all_comments(all_string, new_all, all_nodes[0], comments)
            if noqa_comments:
                first_line, *rest = all_string.split('\n')
                all_string = '\n'.join([first_line + '  ' + ' '.join(comment.string for comment in noqa_comments), *rest])
            lines = source.splitlines()
            first = min(node.lineno for node in all_nodes)
            insert_at = next(node.lineno - 1 for node in tree.body if node.lineno <= first <= node.end_lineno)
            for node in sorted(all_nodes, key=lambda node: (node.lineno, node.col_offset), reverse=True):
                prefix = lines[node.lineno - 1].encode('utf-8')[:node.col_offset].decode('utf-8')
                suffix = lines[node.end_lineno - 1].encode('utf-8')[node.end_col_offset:].decode('utf-8')
                # Trailing comments move with the regenerated declaration.
                suffix = suffix.split('#', 1)[0] if suffix.lstrip().startswith('#') else suffix
                replacement = [prefix + 'pass' + suffix] if prefix.strip() or suffix.strip() or node not in tree.body else []
                lines[node.lineno - 1:node.end_lineno] = replacement
            lines[insert_at:insert_at] = [all_string, '']
            filepath.write_text('\n'.join(lines).rstrip() + '\n')
        return True

    if not all_nodes:
        new_all = sorted(imports, key=_isort_key)
        all_string = format_all_string(new_all, line_length, use_double_quotes, multiline=False)
        if fix:
            with filepath.open("w") as file:
                file.write(source.rstrip() + "\n\n" + all_string + "\n")
        print(f"{filepath}\033[31m error:\033[0m missing __all__")
        return True

    node = all_nodes[0]
    all_var = [elt.value for elt in node.value.elts]
    all_lineno, all_start, all_end = node.lineno, node.lineno - 1, node.end_lineno - 1
    comments = get_all_comments(source, node)
    noqa_symbols = set()
    for comment in comments:
        if comment.start[0] in (node.lineno, node.end_lineno):
            parsed_noqa = parse_noqa(comment.string)
            if parsed_noqa == "ALL":
                return False
            if parsed_noqa:
                noqa_symbols.update(parsed_noqa)

    missing = sorted(imports - set(all_var) - noqa_symbols)
    extra = sorted(set(all_var) - imports - noqa_symbols)
    is_sorted = all_var == sorted(all_var, key=_isort_key)
    print_errors(filepath, all_lineno, is_sorted, missing, extra)
    updated_all = sorted((set(all_var) | set(missing)) - set(extra), key=_isort_key)

    multiline = all_end > all_start
    all_string = format_all_string(updated_all, line_length, use_double_quotes, multiline)

    quote_char = '"' if use_double_quotes else "'"
    wrong_quotes = any(ast.get_source_segment(source, elt) != f"{quote_char}{elt.value}{quote_char}"
                       for elt in node.value.elts)
    if wrong_quotes:
        print(f"{filepath}:{all_lineno}: error: __all__ must use {'double' if use_double_quotes else 'single'} quotes.")
    duplicates = len(all_var) != len(set(all_var))
    if duplicates:
        print(f"{filepath}:{all_lineno}: error: duplicate symbols in __all__.")
    has_errors = bool(not is_sorted or missing or extra or wrong_quotes or duplicates)

    if fix and has_errors:
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
    return has_errors


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


def check_all_in_paths(paths: list[Path], line_length: int, use_double_quotes: bool, fix: bool) -> bool:
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
    has_errors = False
    for path in paths:
        if path.is_file() and path.name == "__init__.py":
            init_files = [path]
        elif path.is_dir():
            init_files = path.rglob("__init__.py")
        else:
            print(f"Warning: {path} is not a valid file or directory.")
            has_errors = True
            continue
        for init_file in init_files:
            try:
                has_errors |= update_all_in_init(init_file, line_length, use_double_quotes, fix)
            except (OSError, UnicodeError, SyntaxError, tokenize.TokenError) as error:
                print(f"{init_file}: error: {error}")
                has_errors = True
    return has_errors


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
    quotes = parser.add_mutually_exclusive_group()
    quotes.add_argument(
        "--double-quotes",
        dest="double_quotes", action="store_true", default=True,
        help="Use double quotes for __all__ (default)",
    )
    quotes.add_argument('--single-quote', '--single-quotes', dest='double_quotes', action='store_false',
                        help='Use single quotes for __all__')
    parser.add_argument(
        "--fix",
        "-f",
        action="store_true",
        help="apply fixes",
    )
    args = parser.parse_args()

    return int(check_all_in_paths([Path(p) for p in args.paths], args.line_length, args.double_quotes, args.fix))


if __name__ == "__main__":
    raise SystemExit(main())
