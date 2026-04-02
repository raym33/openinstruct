import tempfile
import unittest
from pathlib import Path

from openinstruct.tools import WorkspaceTools


class ToolsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.tools = WorkspaceTools(self.root, approval_callback=lambda action: True, approval_policy="ask")

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def test_write_and_read_file(self) -> None:
        write_result = self.tools.write_file("notes.txt", "hello\nworld\n")
        self.assertTrue(write_result.ok)

        read_result = self.tools.read_file("notes.txt", 1, 2)
        self.assertTrue(read_result.ok)
        self.assertIn("hello", read_result.output)
        self.assertIn("world", read_result.output)

    def test_replace_in_file(self) -> None:
        self.tools.write_file("app.py", "value = 1\n")
        result = self.tools.replace_in_file("app.py", "1", "2")
        self.assertTrue(result.ok)
        content = (self.root / "app.py").read_text(encoding="utf-8")
        self.assertIn("2", content)

    def test_glob_files(self) -> None:
        self.tools.write_file("src/app.py", "print('hi')\n")
        result = self.tools.glob_files("**/*.py")
        self.assertTrue(result.ok)
        self.assertIn("src/app.py", result.output)

    def test_memory_roundtrip(self) -> None:
        write_result = self.tools.write_memory("architecture notes")
        self.assertTrue(write_result.ok)

        read_result = self.tools.read_memory()
        self.assertTrue(read_result.ok)
        self.assertIn("architecture notes", read_result.output)

    def test_readonly_command_skips_approval(self) -> None:
        tools = WorkspaceTools(self.root, approval_callback=lambda action: False, approval_policy="ask")
        result = tools.run_command("pwd")
        self.assertTrue(result.ok)
        self.assertIn(str(self.root), result.output)

    def test_mutating_command_requires_approval(self) -> None:
        tools = WorkspaceTools(self.root, approval_callback=lambda action: False, approval_policy="ask")
        result = tools.run("run_command", {"command": "python3 --version"})
        self.assertFalse(result.ok)
        self.assertIn("rejected by user", result.output)

    def test_path_escape_is_blocked(self) -> None:
        result = self.tools.run("read_file", {"path": "../outside.txt"})
        self.assertFalse(result.ok)
        self.assertIn("escapes workspace root", result.output)


if __name__ == "__main__":
    unittest.main()
