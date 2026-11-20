"""The HTTP hook receiver and what gets written into Claude Code's settings.

Run with the others: python -m unittest discover -s tests
"""

import json
import unittest
import urllib.error
import urllib.request
from unittest.mock import patch

import claude_sessions as app


def post(url, koerper, timeout=4):
    daten = json.dumps(koerper).encode("utf-8")
    req = urllib.request.Request(url, data=daten,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status


class EmpfaengerTests(unittest.TestCase):
    def setUp(self):
        self.e = app.HookEmpfaenger()
        self.url = self.e.start()
        self.addCleanup(self.e.stop)
        self.assertTrue(self.url, "Empfaenger nicht gestartet")
        app._HOOK_MELDUNGEN.clear()
        self.addCleanup(app._HOOK_MELDUNGEN.clear)

    def test_listens_on_loopback_only(self):
        self.assertEqual(self.e.server.server_address[0], "127.0.0.1")
        self.assertIn("http://127.0.0.1:", self.url)

    def test_event_is_stored_under_its_session(self):
        post(self.url, {"session_id": "abc123",
                        "hook_event_name": "Notification",
                        "message": "Claude needs your permission"})
        d = app.hook_meldung("abc123")
        self.assertEqual(d["ereignis"], "Notification")
        self.assertEqual(d["zustand"], "awaiting_approval")
        self.assertIn("permission", d["meldung"])

    def test_events_map_to_their_buddy_state(self):
        for ereignis, zustand in (("PreCompact", "compact"),
                                  ("SubagentStart", "subagent"),
                                  ("StopFailure", "error")):
            with self.subTest(ereignis=ereignis):
                post(self.url, {"session_id": ereignis,
                                "hook_event_name": ereignis})
                self.assertEqual(app.hook_meldung(ereignis)["zustand"], zustand)

    def test_wrong_token_is_refused(self):
        # Ohne das koennte jedes Programm auf diesem Rechner dem Buddy
        # Ereignisse unterschieben.
        falsch = self.url.rsplit("/", 1)[0] + "/" + ("0" * 32)
        with self.assertRaises(urllib.error.HTTPError) as fehler:
            post(falsch, {"session_id": "x", "hook_event_name": "Notification"})
        self.assertEqual(fehler.exception.code, 404)
        self.assertIsNone(app.hook_meldung("x"))

    def test_garbage_does_not_take_the_receiver_down(self):
        req = urllib.request.Request(self.url, data=b"kein json",
                                     headers={"Content-Type": "text/plain"})
        with urllib.request.urlopen(req, timeout=4) as r:
            self.assertEqual(r.status, 200)
        self.assertEqual(post(self.url, {"session_id": "danach",
                                         "hook_event_name": "PreCompact"}), 200)
        self.assertIsNotNone(app.hook_meldung("danach"))

    def test_only_the_permission_prompt_counts_as_waiting(self):
        post(self.url, {"session_id": "s1", "hook_event_name": "Notification"})
        self.assertTrue(app._hook_wartet("s1", 0))
        post(self.url, {"session_id": "s2", "hook_event_name": "PreCompact"})
        self.assertIsNone(app._hook_wartet("s2", 0))


class EinstellungenTests(unittest.TestCase):
    """hooks_einrichten schreibt in eine fremde Datei - fremde Hooks
    muessen das unbeschadet ueberstehen."""

    def setUp(self):
        self.datei = {}
        patcher = patch.object(app, "_claude_settings_lesen",
                               side_effect=lambda: json.loads(
                                   json.dumps(self.datei)))
        patcher.start()
        self.addCleanup(patcher.stop)
        speichern = patch.object(app, "save_json",
                                 side_effect=lambda p, d: self.datei.update(
                                     {"__": None}) or self.datei.clear()
                                 or self.datei.update(json.loads(json.dumps(d))))
        speichern.start()
        self.addCleanup(speichern.stop)
        makedirs = patch.object(app.os, "makedirs")
        makedirs.start()
        self.addCleanup(makedirs.stop)

    def test_every_event_gets_an_entry(self):
        app.hooks_einrichten(True, "http://127.0.0.1:1/csb/tok")
        self.assertEqual(sorted(self.datei["hooks"]),
                         sorted(app.HOOK_EREIGNISSE))
        eintrag = self.datei["hooks"]["Notification"][0]["hooks"][0]
        self.assertEqual(eintrag["type"], "http")
        self.assertTrue(eintrag["async"])

    def test_foreign_hooks_survive(self):
        fremd = {"matcher": "Bash",
                 "hooks": [{"type": "command", "command": "echo hallo"}]}
        self.datei = {"hooks": {"Notification": [fremd],
                                "PreToolUse": [fremd]}}
        app.hooks_einrichten(True, "http://127.0.0.1:1/csb/tok")
        self.assertIn(fremd, self.datei["hooks"]["Notification"])
        self.assertEqual(self.datei["hooks"]["PreToolUse"], [fremd])
        app.hooks_einrichten(False)
        self.assertEqual(self.datei["hooks"],
                         {"Notification": [fremd], "PreToolUse": [fremd]})

    def test_the_old_process_hook_is_replaced(self):
        self.datei = {"hooks": {"Notification": [{"matcher": "", "hooks": [
            {"type": "command",
             "command": r'"C:\pfad\ClaudeSessionBrowser.exe" --csb-hook'}]}]}}
        app.hooks_einrichten(True, "http://127.0.0.1:1/csb/tok")
        alle = [h for g in self.datei["hooks"]["Notification"]
                for h in g["hooks"]]
        self.assertEqual(len(alle), 1)
        self.assertEqual(alle[0]["type"], "http")

    def test_switching_off_leaves_nothing_behind(self):
        app.hooks_einrichten(True, "http://127.0.0.1:1/csb/tok")
        app.hooks_einrichten(False)
        self.assertNotIn("hooks", self.datei)
        self.assertFalse(app.hooks_eingerichtet())


if __name__ == "__main__":
    unittest.main()
