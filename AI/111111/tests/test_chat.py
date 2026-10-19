import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import chat

class ChatTests(unittest.TestCase):
    def test_history_roundtrip_and_commands(self):
        with tempfile.TemporaryDirectory() as d:
            old = chat.HISTORY_FILE; chat.HISTORY_FILE = str(Path(d) / "nested" / "history.json")
            try:
                self.assertEqual(chat.load_history(), []); data = [{"role":"user","content":"hi"}]
                chat.save_history(data); self.assertEqual(chat.load_history(), data)
            finally: chat.HISTORY_FILE = old

    def test_history_is_atomically_saved_and_trimmed(self):
        with tempfile.TemporaryDirectory() as d:
            old = chat.HISTORY_FILE; chat.HISTORY_FILE = str(Path(d) / "history.json")
            try:
                data = [{"role": "user", "content": str(i)} for i in range(45)]
                with patch.object(chat.os, "replace", wraps=chat.os.replace) as replace:
                    chat.save_history(data)
                replace.assert_called_once()
                self.assertEqual(chat.load_history(), data[-chat.MAX_HISTORY_MESSAGES:])
                self.assertEqual(list(Path(d).glob("*.tmp")), [])
            finally: chat.HISTORY_FILE = old

    def test_history_filters_invalid_entries_and_bad_json(self):
        with tempfile.TemporaryDirectory() as d:
            old = chat.HISTORY_FILE; chat.HISTORY_FILE = str(Path(d) / "history.json")
            try:
                Path(chat.HISTORY_FILE).write_text(json.dumps([
                    {"role": "user", "content": "ok"},
                    {"role": "system", "content": "bad role"},
                    {"role": "assistant", "content": 3},
                    "bad item",
                ]), encoding="utf-8")
                self.assertEqual(chat.load_history(), [{"role": "user", "content": "ok"}])
                Path(chat.HISTORY_FILE).write_text("{bad", encoding="utf-8")
                self.assertEqual(chat.load_history(), [])
            finally: chat.HISTORY_FILE = old

if __name__ == "__main__": unittest.main()
