import json
import tempfile
import unittest
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import chat

class ChatTests(unittest.TestCase):
    def test_history_roundtrip_and_commands(self):
        with tempfile.TemporaryDirectory() as d:
            old = chat.HISTORY_FILE; chat.HISTORY_FILE = str(Path(d) / "history.json")
            try:
                self.assertEqual(chat.load_history(), []); data = [{"role":"user","content":"hi"}]
                chat.save_history(data); self.assertEqual(chat.load_history(), data)
            finally: chat.HISTORY_FILE = old

if __name__ == "__main__": unittest.main()
