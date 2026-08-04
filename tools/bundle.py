"""Merge the lanphone package into one standalone script.

    python tools/bundle.py            # writes LANPhone_single.py
    python tools/bundle.py --check    # fails if the file is out of date

The package stays the source of truth; this only flattens it.  Rewriting is
done over the *token* stream, not with regular expressions, so a module name
appearing inside a string or a comment is never touched.

Two things make a flat file possible:

* Cross-module references like ``theme.BG`` lose their prefix and become plain
  ``BG``.  This is only safe because no local variable shadows a module name -
  ``check_no_shadowing`` proves that and fails the build if one ever appears.
* Four names collide between modules and are renamed on the way in (RENAMES).
"""

from __future__ import annotations

import argparse
import ast
import io
import pathlib
import sys
import tokenize

ROOT = pathlib.Path(__file__).resolve().parent.parent
PACKAGE = ROOT / "lanphone"
OUTPUT = ROOT / "LANPhone_single.py"

# Dependency order: a module may only use ones listed before it.
MODULES = [
    "config",
    "i18n",
    "theme",
    "protocol",
    "jitter",
    "resample",
    "appicon",
    "winchrome",
    "audio",
    "net",
    "phone",
    "gui",
    "__main__",
]

# Modules referred to by a prefix (``theme.BG``), and the name used for them.
# jitter and resample are absent on purpose: they are imported by name
# (``from .jitter import JitterBuffer``), never as a prefix, so a local
# variable called ``jitter`` is perfectly fine and must not be rewritten.
ALIASES = {
    "theme": "theme",
    "protocol": "protocol",
    "net": "net",
    "appicon": "appicon",
    "winchrome": "winchrome",
    "audio": "audiolib",
}

# (module, original name) -> flat name.  Only for names that would collide.
RENAMES = {
    ("theme", "apply"): "apply_theme",
    ("appicon", "apply"): "apply_app_icon",
    ("winchrome", "apply"): "apply_window_chrome",
    ("theme", "WATERMARK"): "WATERMARK_STYLE",
    ("protocol", "RINGING"): "MSG_RINGING",
    ("gui", "main"): "run_gui",
}

HEADER = '''"""LAN Phone - voice calls between two PCs on the same local network.

This single file is GENERATED - do not edit it by hand.  The real source is
the ``lanphone`` package; regenerate with::

    python tools/bundle.py

It exists so the whole program can be copied around as one file.  Run it with
``python LANPhone_single.py`` exactly like ``python main.py``.
"""

from __future__ import annotations
'''


def module_path(name: str) -> pathlib.Path:
    return PACKAGE / f"{name}.py"


def check_no_shadowing() -> None:
    """Fail loudly if a variable shadows a module name.

    Stripping the ``theme.`` in ``theme.BG`` is only correct while no local
    called ``theme`` exists.  ``net.py`` once had a local ``net`` holding an
    ip_network, and flattening it would have silently produced wrong code.
    """
    watched = set(ALIASES) | set(ALIASES.values())
    problems = []
    for name in MODULES:
        tree = ast.parse(module_path(name).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
                if node.id in watched:
                    problems.append(f"{name}.py:{node.lineno} assigns to '{node.id}'")
            elif isinstance(node, ast.arg) and node.arg in watched:
                problems.append(f"{name}.py:{node.lineno} has a parameter '{node.arg}'")
    if problems:
        raise SystemExit(
            "cannot flatten: these shadow a module name, so dropping the prefix "
            "would change meaning:\n  " + "\n  ".join(problems)
        )


def collect_imports() -> list[str]:
    """Every non-package import, merged and deduplicated, for the top."""
    plain: set[str] = set()
    from_imports: dict[str, set[str]] = {}
    for name in MODULES:
        tree = ast.parse(module_path(name).read_text(encoding="utf-8"))
        for node in tree.body:  # top level only; lazy imports stay where they are
            if isinstance(node, ast.Import):
                for alias in node.names:
                    as_part = f" as {alias.asname}" if alias.asname else ""
                    plain.add(f"import {alias.name}{as_part}")
            elif isinstance(node, ast.ImportFrom):
                if node.level:  # from . import ... - dropped, the code is inlined
                    continue
                if node.module == "__future__":
                    continue  # already in the header, and it has to stay first
                names = from_imports.setdefault(node.module, set())
                for alias in node.names:
                    names.add(alias.name + (f" as {alias.asname}" if alias.asname else ""))
    lines = sorted(plain)
    lines += [
        f"from {module} import {', '.join(sorted(names))}"
        for module, names in sorted(from_imports.items())
    ]
    return lines


def rewrite(name: str, source: str) -> str:
    """Drop package-import lines, strip module prefixes, apply renames."""
    rename_here = {old: new for (mod, old), new in RENAMES.items() if mod == name}
    out: list[tokenize.TokenInfo] = []
    tokens = list(tokenize.generate_tokens(io.StringIO(source).readline))

    index = 0
    while index < len(tokens):
        token = tokens[index]

        # "<alias>.<attr>" -> "<attr>", with a rename if the target has one.
        if token.type == tokenize.NAME and token.string in ALIASES.values():
            following = tokens[index + 1 : index + 3]
            if (
                len(following) == 2
                and following[0].type == tokenize.OP
                and following[0].string == "."
                and following[1].type == tokenize.NAME
            ):
                module = next(m for m, a in ALIASES.items() if a == token.string)
                attr = following[1].string
                out.append(
                    token._replace(string=RENAMES.get((module, attr), attr), type=tokenize.NAME)
                )
                index += 3
                continue

        # A name this module defines that had to be renamed.
        if token.type == tokenize.NAME and token.string in rename_here:
            previous = out[-1] if out else None
            is_attribute = previous is not None and previous.string == "."
            if not is_attribute:
                out.append(token._replace(string=rename_here[token.string]))
                index += 1
                continue

        out.append(token)
        index += 1

    return tokenize.untokenize(out)


def resolve_imports(source: str) -> str:
    """Deal with import statements, precisely, using the parse tree.

    * top-level plain imports: dropped, they were hoisted to the header
    * imports inside a function: kept - they are lazy on purpose
    * relative (package) imports at any level: dropped, since the code they
      pointed at is now in the same namespace.  The exception is an aliased
      one such as ``from .gui import main as gui_main``, which becomes a plain
      assignment so the alias still refers to something.
    """
    tree = ast.parse(source)
    replacements: dict[int, list[str]] = {}
    drop: set[int] = set()

    for node in ast.walk(tree):
        if not isinstance(node, (ast.Import, ast.ImportFrom)):
            continue
        relative = isinstance(node, ast.ImportFrom) and node.level
        if not relative and node.col_offset > 0:
            continue  # a deliberate lazy import of something outside the package
        span = range(node.lineno, (node.end_lineno or node.lineno) + 1)
        drop.update(span)
        if not relative:
            continue
        indent = " " * node.col_offset
        lines = []
        if node.module is not None:  # "from .gui import main as gui_main"
            for alias in node.names:
                if alias.asname:
                    target = RENAMES.get((node.module, alias.name), alias.name)
                    lines.append(f"{indent}{alias.asname} = {target}")
        replacements[node.lineno] = lines

    out = []
    for number, line in enumerate(source.splitlines(), start=1):
        if number in replacements:
            out.extend(replacements[number])
        elif number not in drop:
            out.append(line)
    return "\n".join(out)


def body_of(name: str) -> str:
    """A module's code, flattened, without its docstring or package imports."""
    source = module_path(name).read_text(encoding="utf-8")
    tree = ast.parse(source)
    if ast.get_docstring(tree, clean=False) is not None:
        source = "\n".join(source.splitlines()[tree.body[0].end_lineno :])
    return rewrite(name, resolve_imports(source)).strip("\n")


def verify(bundled: str) -> None:
    """Prove nothing was missed, by reading the result back as a parse tree.

    The token pass can only rewrite what the tokenizer shows it, so this is
    the backstop: it walks the generated code and fails if any reference to a
    module namespace, or any package import, survived.  ast sees inside
    f-strings on every Python version, which is exactly where the token pass
    is blind before 3.12.
    """
    tree = ast.parse(bundled)
    leftovers = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
            if node.value.id in ALIASES.values():
                leftovers.append(f"line {node.lineno}: {node.value.id}.{node.attr}")
        elif isinstance(node, ast.ImportFrom) and node.level:
            leftovers.append(f"line {node.lineno}: relative import survived")
    if leftovers:
        raise SystemExit(
            "the bundle is not self-contained - these would fail at runtime:\n  "
            + "\n  ".join(leftovers)
        )


def build() -> str:
    if sys.version_info < (3, 12):
        raise SystemExit(
            f"this tool needs Python 3.12 or newer (running {sys.version.split()[0]}).\n"
            "Before 3.12 an f-string is a single opaque token, so a reference like\n"
            '    f"{net.broadcast_targets(ip)}"\n'
            "is invisible to the rewriter and the bundle comes out broken.\n"
            "The app itself still runs on 3.9+; only building the single file needs 3.12."
        )
    check_no_shadowing()
    parts = [HEADER, "", *collect_imports(), ""]
    for name in MODULES:
        title = "entry point" if name == "__main__" else f"from lanphone/{name}.py"
        parts += ["", "# " + "-" * 74, f"# {title}", "# " + "-" * 74, "", body_of(name)]
    parts += [
        "",
        "",
        'if __name__ == "__main__":',
        "    raise SystemExit(main())",
        "",
    ]
    return "\n".join(parts)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="verify the file is up to date")
    args = parser.parse_args(argv)

    bundled = build()
    compile(bundled, str(OUTPUT), "exec")  # never write a file that will not parse
    verify(bundled)

    if args.check:
        current = OUTPUT.read_text(encoding="utf-8") if OUTPUT.exists() else ""
        if current != bundled:
            print(f"{OUTPUT.name} is out of date - run: python tools/bundle.py", file=sys.stderr)
            return 1
        print(f"{OUTPUT.name} is up to date ({len(bundled.splitlines())} lines)")
        return 0

    OUTPUT.write_text(bundled, encoding="utf-8")
    print(f"wrote {OUTPUT.name} ({len(bundled.splitlines())} lines)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
