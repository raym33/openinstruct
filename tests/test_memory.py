import tempfile
import unittest
from pathlib import Path

from openinstruct.memory import SQLiteMemoryBackend, extract_memory_facts


class MemoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name).resolve()

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def test_extract_memory_facts_prefers_durable_lines(self) -> None:
        facts = extract_memory_facts(
            "remember this setup",
            "- Use Obsidian as the wiki frontend.\n- Store outputs in outputs/slides.\n- okay",
        )
        self.assertTrue(any("Obsidian" in fact for fact in facts))
        self.assertTrue(any("outputs/slides" in fact for fact in facts))

    def test_sqlite_memory_backend_roundtrip(self) -> None:
        backend = SQLiteMemoryBackend(self.root / "memory.sqlite3", user_id="user", agent_id="agent")
        backend.store(
            "Use qwen for coding tasks.",
            "",
            session_name="s1",
            agent_label="primary",
        )
        backend.store(
            "Keep the wiki in markdown under wiki/.",
            "",
            session_name="s1",
            agent_label="primary",
        )
        recent = backend.recent(session_name="s1", agent_label="primary", limit=5)
        search = backend.search("wiki markdown", session_name="s1", agent_label="primary", limit=5)
        self.assertEqual(len(recent), 2)
        self.assertEqual(search[0].text, "Keep the wiki in markdown under wiki/.")
        self.assertEqual(search[0].backend, "sqlite")


if __name__ == "__main__":
    unittest.main()
