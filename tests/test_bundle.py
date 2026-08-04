"""The generated single-file build.

The package is the source of truth; LANPhone_single.py is produced from it by
tools/bundle.py.  These tests exist so the two cannot drift apart silently.
"""

import ast
import pathlib
import subprocess
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
BUNDLE = ROOT / "LANPhone_single.py"
TOOL = ROOT / "tools" / "bundle.py"

# Before 3.12 an f-string is one opaque token, so the rewriter cannot see
# inside it and the tool refuses to run at all.
CAN_BUILD = sys.version_info >= (3, 12)


class BundleFileTest(unittest.TestCase):
    def setUp(self):
        if not BUNDLE.exists():
            self.fail(f"{BUNDLE.name} is missing - run: python tools/bundle.py")
        self.source = BUNDLE.read_text(encoding="utf-8")

    def test_it_is_valid_python(self):
        compile(self.source, str(BUNDLE), "exec")

    def test_it_is_self_contained(self):
        """No package imports and no module-qualified names may survive."""
        tree = ast.parse(self.source)
        aliases = {"theme", "protocol", "net", "appicon", "winchrome", "audiolib"}
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                self.assertFalse(node.level, f"relative import at line {node.lineno}")
                self.assertNotEqual(node.module, "lanphone", f"line {node.lineno}")
            if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
                self.assertNotIn(
                    node.value.id,
                    aliases,
                    f"line {node.lineno}: {node.value.id}.{node.attr} would not resolve",
                )

    def test_the_colliding_names_were_renamed_apart(self):
        tree = ast.parse(self.source)
        defined = [
            node.name
            for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.ClassDef))
        ]
        for name in ("apply_theme", "apply_app_icon", "apply_window_chrome", "run_gui"):
            self.assertIn(name, defined, f"{name} is missing from the bundle")
        self.assertEqual(len(defined), len(set(defined)), "a name is defined twice")

    def test_the_pieces_that_matter_are_all_there(self):
        tree = ast.parse(self.source)
        top_level = {
            node.name
            for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.ClassDef))
        }
        for name in ("Phone", "AudioEngine", "PhoneApp", "JitterBuffer", "Resampler", "Settings"):
            self.assertIn(name, top_level, f"{name} did not make it into the bundle")

    def test_it_declares_itself_generated(self):
        self.assertIn("GENERATED", self.source[:400])


@unittest.skipUnless(CAN_BUILD, "the bundler needs Python 3.12 or newer")
class BundleIsCurrentTest(unittest.TestCase):
    def test_regenerating_produces_the_committed_file(self):
        """Catches a package change that was not bundled."""
        result = subprocess.run(
            [sys.executable, str(TOOL), "--check"],
            capture_output=True,
            text=True,
            cwd=ROOT,
        )
        self.assertEqual(
            result.returncode,
            0,
            f"{result.stdout}{result.stderr}".strip() or "bundle is stale",
        )


class BundlerRefusesOldPythonTest(unittest.TestCase):
    def test_the_version_guard_is_present(self):
        """A silently broken bundle is worse than no bundle."""
        source = TOOL.read_text(encoding="utf-8")
        self.assertIn("sys.version_info < (3, 12)", source)


if __name__ == "__main__":
    unittest.main()
