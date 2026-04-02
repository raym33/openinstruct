import tempfile
import unittest
from pathlib import Path

from openinstruct.knowledge import (
    default_query_output_path,
    init_knowledge_base,
    knowledge_status,
)


class KnowledgeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name).resolve()

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def test_init_knowledge_base_creates_expected_layout(self) -> None:
        payload = init_knowledge_base(self.root, name="Research Wiki")
        self.assertEqual(payload["root"], str(self.root))
        self.assertTrue((self.root / ".openinstruct" / "kb.json").exists())
        self.assertTrue((self.root / "raw").is_dir())
        self.assertTrue((self.root / "wiki" / "sources").is_dir())
        self.assertTrue((self.root / "wiki" / "concepts").is_dir())
        self.assertTrue((self.root / "wiki" / "queries").is_dir())
        self.assertTrue((self.root / "outputs" / "slides").is_dir())
        self.assertTrue((self.root / "outputs" / "figures").is_dir())

    def test_knowledge_status_counts_files(self) -> None:
        init_knowledge_base(self.root)
        (self.root / "raw" / "paper.md").write_text("# paper\n", encoding="utf-8")
        (self.root / "wiki" / "concepts" / "agents.md").write_text("# agents\n", encoding="utf-8")
        (self.root / "wiki" / "queries" / "q1.md").write_text("# q1\n", encoding="utf-8")
        (self.root / "outputs" / "slides" / "deck.md").write_text("# slide\n", encoding="utf-8")
        (self.root / "outputs" / "figures" / "plot.png").write_bytes(b"png")
        payload = knowledge_status(self.root)
        self.assertTrue(payload["configured"])
        self.assertGreaterEqual(payload["raw_files"], 1)
        self.assertGreaterEqual(payload["wiki_articles"], 3)
        self.assertGreaterEqual(payload["query_articles"], 1)
        self.assertEqual(payload["slides"], 1)
        self.assertEqual(payload["figures"], 1)

    def test_default_query_output_path_respects_format(self) -> None:
        init_knowledge_base(self.root)
        markdown_path = default_query_output_path(self.root, "What is Hermes agent?")
        marp_path = default_query_output_path(self.root, "What is Hermes agent?", output_format="marp")
        self.assertEqual(markdown_path.parent, self.root / "wiki" / "queries")
        self.assertEqual(marp_path.parent, self.root / "outputs" / "slides")
        self.assertEqual(markdown_path.suffix, ".md")
        self.assertEqual(marp_path.suffix, ".md")


if __name__ == "__main__":
    unittest.main()
