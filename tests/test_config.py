import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from working_groups.ran1.config import load_config


class ExtraFilesConfigTests(unittest.TestCase):
    def test_missing_key_returns_empty_list(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "working_groups.ran1.config.json"
            path.write_text("{}", encoding="utf-8")
            cfg = load_config(path=path)
        self.assertEqual(cfg["extra_files"], [])

    def test_invalid_entry_skipped(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "working_groups.ran1.config.json"
            path.write_text(
                json.dumps(
                    {
                        "extra_files": [
                            {"url": "https://example.org/schedule.docx"},
                            {"url": "https://example.org/notes.docx", "type": "zzz"},
                            {"type": "schedule"},
                            {"url": "   ", "type": "schedule"},
                            "not-a-dict",
                        ]
                    }
                ),
                encoding="utf-8",
            )
            cfg = load_config(path=path)
        self.assertEqual(cfg["extra_files"], [])

    def test_valid_entries_round_trip_with_defaults(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "working_groups.ran1.config.json"
            entries = [
                {"url": "https://example.org/schedule.docx", "type": "schedule"},
                {
                    "url": "https://example.org/chair_notes.docx",
                    "type": "chair_notes",
                    "person_name": "Hiroki",
                    "is_main": True,
                    "name": "notes.docx",
                },
            ]
            path.write_text(json.dumps({"extra_files": entries}), encoding="utf-8")
            cfg = load_config(path=path)
        self.assertEqual(len(cfg["extra_files"]), 2)
        self.assertEqual(
            cfg["extra_files"][0],
            {
                "url": "https://example.org/schedule.docx",
                "type": "schedule",
                "name": None,
                "person_name": None,
                "is_main": True,  # no person_name → main by default
            },
        )
        self.assertEqual(
            cfg["extra_files"][1],
            {
                "url": "https://example.org/chair_notes.docx",
                "type": "chair_notes",
                "name": "notes.docx",
                "person_name": "Hiroki",
                "is_main": True,
            },
        )

    def test_is_main_auto_derivation(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "working_groups.ran1.config.json"
            entries = [
                # no person_name, no is_main → True
                {"url": "https://example.org/a.docx", "type": "schedule"},
                # person_name, no is_main → False
                {
                    "url": "https://example.org/b.docx",
                    "type": "schedule",
                    "person_name": "Hiroki",
                },
                # person_name + explicit True → True (explicit wins)
                {
                    "url": "https://example.org/c.docx",
                    "type": "schedule",
                    "person_name": "Hiroki",
                    "is_main": True,
                },
                # no person_name + explicit False → False (explicit wins)
                {
                    "url": "https://example.org/d.docx",
                    "type": "chair_notes",
                    "is_main": False,
                },
            ]
            path.write_text(json.dumps({"extra_files": entries}), encoding="utf-8")
            cfg = load_config(path=path)
        self.assertEqual(
            [e["is_main"] for e in cfg["extra_files"]], [True, False, True, False]
        )

    def test_mixed_valid_and_invalid(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "working_groups.ran1.config.json"
            path.write_text(
                json.dumps(
                    {
                        "extra_files": [
                            {"url": "https://example.org/a.docx", "type": "schedule"},
                            {"url": "https://example.org/b.docx", "type": "chair_notes"},
                            {"url": "https://example.org/c.docx", "type": "bogus"},
                        ]
                    }
                ),
                encoding="utf-8",
            )
            cfg = load_config(path=path)
        self.assertEqual(
            [e["url"] for e in cfg["extra_files"]],
            [
                "https://example.org/a.docx",
                "https://example.org/b.docx",
            ],
        )

    def test_env_override_replaces_file_value(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "working_groups.ran1.config.json"
            path.write_text(
                json.dumps(
                    {
                        "extra_files": [
                            {"url": "https://from-file.org/x.docx", "type": "schedule"}
                        ]
                    }
                ),
                encoding="utf-8",
            )
            env_value = json.dumps(
                [{"url": "https://from-env.org/y.docx", "type": "chair_notes"}]
            )
            with patch.dict(os.environ, {"SCHEDULE_EXTRA_FILES": env_value}):
                cfg = load_config(path=path)
        self.assertEqual(
            [e["url"] for e in cfg["extra_files"]], ["https://from-env.org/y.docx"]
        )
        self.assertEqual(cfg["extra_files"][0]["type"], "chair_notes")

    def test_env_invalid_json_keeps_file_value(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "working_groups.ran1.config.json"
            path.write_text(
                json.dumps(
                    {
                        "extra_files": [
                            {"url": "https://from-file.org/x.docx", "type": "schedule"}
                        ]
                    }
                ),
                encoding="utf-8",
            )
            with patch.dict(os.environ, {"SCHEDULE_EXTRA_FILES": "not-json[}"}):
                cfg = load_config(path=path)
        self.assertEqual(
            [e["url"] for e in cfg["extra_files"]], ["https://from-file.org/x.docx"]
        )



