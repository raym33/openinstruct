import threading
import time
import unittest

from openinstruct.locking import WorkspaceLockManager


class LockingTests(unittest.TestCase):
    def test_lock_manager_serializes_same_key(self) -> None:
        manager = WorkspaceLockManager()
        entered = []
        release_first = threading.Event()

        def first_worker():
            with manager.hold(["path:/tmp/file.py"], "agent-a"):
                entered.append("first")
                release_first.wait(timeout=1)

        def second_worker():
            with manager.hold(["path:/tmp/file.py"], "agent-b"):
                entered.append("second")

        t1 = threading.Thread(target=first_worker)
        t2 = threading.Thread(target=second_worker)
        t1.start()
        time.sleep(0.05)
        t2.start()
        time.sleep(0.05)
        self.assertEqual(entered, ["first"])
        release_first.set()
        t1.join(timeout=1)
        t2.join(timeout=1)
        self.assertEqual(entered, ["first", "second"])


if __name__ == "__main__":
    unittest.main()
