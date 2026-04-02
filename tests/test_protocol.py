import unittest

from openinstruct.protocol import ProtocolError, parse_model_response


class ProtocolTests(unittest.TestCase):
    def test_parse_fenced_json(self) -> None:
        raw = """```json
        {
          "summary": "inspect repo",
          "actions": [{"tool": "list_dir", "args": {"path": "."}}],
          "final": ""
        }
        ```"""
        reply = parse_model_response(raw)
        self.assertEqual(reply.summary, "inspect repo")
        self.assertEqual(reply.actions[0].tool, "list_dir")
        self.assertEqual(reply.final, "")

    def test_parse_plain_json_object(self) -> None:
        raw = '{"summary":"done","actions":[],"final":"ok"}'
        reply = parse_model_response(raw)
        self.assertEqual(reply.final, "ok")

    def test_invalid_json_raises(self) -> None:
        with self.assertRaises(ProtocolError):
            parse_model_response("not json")


if __name__ == "__main__":
    unittest.main()
