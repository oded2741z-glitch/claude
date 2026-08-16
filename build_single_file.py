#!/usr/bin/env python3
"""Bundles the modules into two standalone scripts, one per machine.

    python build_single_file.py

Produces `intercom_A.py` (signalling server + peer) and `intercom_B.py`
(peer only). Each one is a complete program: copy that single file to the
machine and run it, nothing else from this repository is needed.

The modules stay the source of truth. Nothing is retyped here - the builder
concatenates the real source, dropping only the cross-module imports and the
`if __name__` guards, so the bundles cannot drift into a second, subtly
different implementation. `tests/selftest.py` fails if they are out of date.
"""

import ast
import os
import sys
from typing import Dict, List, Set, Tuple

ROOT: str = os.path.dirname(os.path.abspath(__file__))

# סדר תלויות: כל מודול משתמש רק במה שהוגדר לפניו
MODULES: Tuple[str, ...] = ("intercom_core.py", "txt_bridge.py", "signalling.py", "node.py")
LOCAL_MODULES: Set[str] = {name[:-3] for name in MODULES}
TARGETS: Dict[str, str] = {"intercom_A.py": "a", "intercom_B.py": "b"}

HEADER: str = '''#!/usr/bin/env python3
"""Headless P2P intercom - {title}.

GENERATED FILE - do not edit. Built from {sources}
by build_single_file.py; edit those and rebuild.

    pip install sounddevice numpy      # Linux also: apt install libportaudio2
    python {name}{example}

Control it by editing {control}, which any other program may rewrite while
this is running; the live state is written back to {status}.
"""
'''

TITLES: Dict[str, str] = {
    "a": "computer A (signalling server + peer)",
    "b": "computer B (peer only)",
}
EXAMPLES: Dict[str, str] = {"a": "", "b": " --server-ip <computer A's address>"}


def _drop_ranges(source: str) -> Set[int]:
    """1-based line numbers to remove: cross-module imports and the main guard."""
    tree = ast.parse(source)
    drop: Set[int] = set()
    for node in tree.body:
        if isinstance(node, ast.Import):
            if any(alias.name.split(".")[0] in LOCAL_MODULES for alias in node.names):
                drop.update(range(node.lineno, node.end_lineno + 1))
        elif isinstance(node, ast.ImportFrom):
            if (node.module or "").split(".")[0] in LOCAL_MODULES:
                drop.update(range(node.lineno, node.end_lineno + 1))
        elif isinstance(node, ast.If) and _is_main_guard(node):
            drop.update(range(node.lineno, node.end_lineno + 1))
    return drop


def _is_main_guard(node: ast.If) -> bool:
    test = node.test
    return (isinstance(test, ast.Compare)
            and isinstance(test.left, ast.Name) and test.left.id == "__name__")


def _strip(path: str) -> str:
    """The module source minus the lines the bundle must not contain.

    מסירים לפי מספרי שורות ולא לפי צמתי AST, כדי שהערות ושורות ריקות
    יישמרו בדיוק כפי שהן.
    """
    with open(path, "r", encoding="utf-8") as f:
        source = f.read()
    drop = _drop_ranges(source)
    kept = [line for number, line in enumerate(source.splitlines(), start=1)
            if number not in drop]
    while kept and not kept[0].strip():
        kept.pop(0)
    while kept and not kept[-1].strip():
        kept.pop()
    return "\n".join(kept)


def render(role: str) -> str:
    """The full text of the standalone script for one role."""
    name = next(n for n, r in TARGETS.items() if r == role)
    parts: List[str] = [HEADER.format(
        title=TITLES[role],
        sources=", ".join(MODULES),
        name=name,
        example=EXAMPLES[role],
        control=f"control_{role.upper()}.txt",
        status=f"status_{role.upper()}.txt",
    )]
    for module in MODULES:
        parts.append(f"\n# {'=' * 74}\n# {module}\n# {'=' * 74}\n")
        parts.append(_strip(os.path.join(ROOT, module)))
        parts.append("\n")
    parts.append('\nif __name__ == "__main__":\n'
                 f'    main(default_role="{role}")\n')
    return "".join(parts)


def check() -> bool:
    """True when the committed bundles match what the modules produce now."""
    stale = []
    for name, role in TARGETS.items():
        path = os.path.join(ROOT, name)
        try:
            with open(path, "r", encoding="utf-8") as f:
                current = f.read()
        except OSError:
            stale.append(name)
            continue
        if current != render(role):
            stale.append(name)
    if stale:
        print("Out of date: " + ", ".join(stale) + "  (run: python build_single_file.py)")
    return not stale


def main() -> int:
    if "--check" in sys.argv:
        return 0 if check() else 1
    for name, role in TARGETS.items():
        path = os.path.join(ROOT, name)
        text = render(role)
        compile(text, name, "exec")      # לא כותבים קובץ שאפילו לא מתקמפל
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
        print(f"Wrote {name} ({len(text.splitlines())} lines, role {role.upper()})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
