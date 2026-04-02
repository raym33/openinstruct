import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from openinstruct.config import load_settings


class ConfigTests(unittest.TestCase):
    def test_openinstruct_home_env_overrides_default_home(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            custom_home = Path(tempdir) / "oi-home"
            with patch.dict(os.environ, {"OPENINSTRUCT_HOME": str(custom_home)}, clear=False):
                settings = load_settings()
            self.assertEqual(settings.home, custom_home.resolve())

    def test_memory_backend_env_is_loaded(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            custom_home = Path(tempdir) / "oi-home"
            with patch.dict(
                os.environ,
                {
                    "OPENINSTRUCT_HOME": str(custom_home),
                    "OPENINSTRUCT_MEMORY_BACKEND": "mem0",
                },
                clear=False,
            ):
                settings = load_settings()
            self.assertEqual(settings.memory_backend, "mem0")

    def test_sqlite_memory_backend_and_policy_env_are_loaded(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            custom_home = Path(tempdir) / "oi-home"
            with patch.dict(
                os.environ,
                {
                    "OPENINSTRUCT_HOME": str(custom_home),
                    "OPENINSTRUCT_MEMORY_BACKEND": "sqlite",
                    "OPENINSTRUCT_MEMORY_POLICY": "all",
                },
                clear=False,
            ):
                settings = load_settings()
            self.assertEqual(settings.memory_backend, "sqlite")
            self.assertEqual(settings.memory_policy, "all")

    def test_task_retries_env_is_loaded(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            custom_home = Path(tempdir) / "oi-home"
            with patch.dict(
                os.environ,
                {
                    "OPENINSTRUCT_HOME": str(custom_home),
                    "OPENINSTRUCT_TASK_RETRIES": "3",
                },
                clear=False,
            ):
                settings = load_settings()
            self.assertEqual(settings.task_retries, 3)


if __name__ == "__main__":
    unittest.main()
