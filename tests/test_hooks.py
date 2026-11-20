"""Hook events: what we store, and what we write into Claude Code's settings.

Run with the others: python -m unittest discover -s tests
"""

import json
import os
import tempfile
import unittest
from unittest.mock import patch

import claude_sessions as app


class EintragTests(unittest.TestCase):
    def test_event_maps_to_its_buddy_state(self):
        for ereignis, zustand in (("Notification", "awaiting_approval"),
                                  ("PreCompact", "compact"),
                                  ("SubagentStart", "subagent"),
                                  ("StopFailure", "error"),
                                  ("PostCompact", None)):
            with self.subTest(ereignis=ereignis):
                d = app.hook_eintrag({"hook_event_name": ereignis})
                self.assertEqual(d["zustand"], zustand)

    def test_long_fields_are_cut(self):
        d = app.hook_eintrag({"hook_event_name": "Notification",
                              "message": "x" * 900, "error": "y" * 900})
        self.assertEqual(len(d["meldung"]), 300)
        self.assertEqual(len(d["grund"]), 80)

    def test_unknown_event_has_no_state(self):
        self.assertIsNone(app.hook_eintrag({"hook_event_name": "Was?"})["zustand"])


class SpeicherTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        p = patch.object(app, "HOOK_DIR", self.dir)
        p.start()
        self.addCleanup(p.stop)

    def schreibe(self, sid, daten):
        with open(os.path.join(self.dir, sid + ".json"), "w",
                  encoding="utf-8") as fh:
            json.dump(daten, fh)

    def test_reads_back_what_was_written(self):
        self.schreibe("abc", app.hook_eintrag(
            {"hook_event_name": "PreCompact"}))
        self.assertEqual(app.hook_meldung("abc")["zustand"], "compact")

    def test_older_files_without_the_state_field_still_work(self):
        # Dateien aus Fassungen vor den neuen Ereignissen.
        self.schreibe("alt", {"ereignis": "Notification", "meldung": "",
                              "zeit": app.time.time()})
        self.assertEqual(app.hook_meldung("alt")["zustand"],
                         "awaiting_approval")

    def test_missing_or_broken_file_is_no_answer(self):
        self.assertIsNone(app.hook_meldung("gibtsnicht"))
        with open(os.path.join(self.dir, "kaputt.json"), "w") as fh:
            fh.write("{kein json")
        self.assertIsNone(app.hook_meldung("kaputt"))

    def test_only_the_permission_prompt_counts_as_waiting(self):
        jetzt = app.time.time()
        self.schreibe("s1", app.hook_eintrag({"hook_event_name": "Notification"}))
        self.assertTrue(app._hook_wartet("s1", jetzt - 60))
        self.schreibe("s2", app.hook_eintrag({"hook_event_name": "PreCompact"}))
        self.assertIsNone(app._hook_wartet("s2", jetzt - 60))

    def test_a_stale_message_is_no_answer(self):
        d = app.hook_eintrag({"hook_event_name": "Notification"})
        d["zeit"] = app.time.time() - app.HOOK_FRESH_S - 10
        self.schreibe("s3", d)
        self.assertIsNone(app._hook_wartet("s3", 0))


class EinstellungenTests(unittest.TestCase):
    """hooks_einrichten schreibt in eine fremde Datei - fremde Hooks muessen
    das unbeschadet ueberstehen."""

    def setUp(self):
        self.datei = {}
        lesen = patch.object(app, "_claude_settings_lesen",
                             side_effect=lambda: json.loads(
                                 json.dumps(self.datei)))
        lesen.start()
        self.addCleanup(lesen.stop)

        def speichern(_pfad, d):
            self.datei.clear()
            self.datei.update(json.loads(json.dumps(d)))

        sp = patch.object(app, "save_json", side_effect=speichern)
        sp.start()
        self.addCleanup(sp.stop)
        md = patch.object(app.os, "makedirs")
        md.start()
        self.addCleanup(md.stop)

    def test_every_event_gets_an_entry(self):
        app.hooks_einrichten(True)
        self.assertEqual(sorted(self.datei["hooks"]),
                         sorted(app.HOOK_EREIGNISSE))
        eintrag = self.datei["hooks"]["PreCompact"][0]["hooks"][0]
        self.assertEqual(eintrag["type"], "command")
        self.assertIn(app._HOOK_MARKE, eintrag["command"])

    def test_foreign_hooks_survive_both_ways(self):
        fremd = {"matcher": "Bash",
                 "hooks": [{"type": "command", "command": "echo hallo"}]}
        self.datei = {"hooks": {"Notification": [fremd], "PreToolUse": [fremd]}}
        app.hooks_einrichten(True)
        self.assertIn(fremd, self.datei["hooks"]["Notification"])
        self.assertEqual(self.datei["hooks"]["PreToolUse"], [fremd])
        app.hooks_einrichten(False)
        self.assertEqual(self.datei["hooks"],
                         {"Notification": [fremd], "PreToolUse": [fremd]})

    def test_an_http_entry_from_the_interim_version_is_cleaned_up(self):
        self.datei = {"hooks": {"Notification": [{"matcher": "", "hooks": [
            {"type": "http", "url": "http://127.0.0.1:5/csb/abc",
             "async": True}]}]}}
        app.hooks_einrichten(True)
        alle = [h for g in self.datei["hooks"]["Notification"]
                for h in g["hooks"]]
        self.assertEqual([h["type"] for h in alle], ["command"])

    def test_switching_off_leaves_nothing_behind(self):
        app.hooks_einrichten(True)
        app.hooks_einrichten(False)
        self.assertNotIn("hooks", self.datei)
        self.assertFalse(app.hooks_eingerichtet())


if __name__ == "__main__":
    unittest.main()
