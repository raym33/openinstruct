import tempfile
import unittest
from pathlib import Path

from openinstruct.session import SessionStore


class SessionStoreTests(unittest.TestCase):
    def test_list_returns_empty_when_directory_does_not_exist(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            home = Path(tempdir) / "missing-home"
            store = SessionStore(home)
            self.assertEqual(store.list(), [])


if __name__ == "__main__":
    unittest.main()
