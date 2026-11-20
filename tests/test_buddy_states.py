"""Which animation the buddy picks for what Claude Code is doing.

Run with the others: python -m unittest discover -s tests
"""

import json
import os
import unittest

import claude_sessions as app


class WerkzeugZuordnungTests(unittest.TestCase):
    def test_tools_map_to_their_phase(self):
        for werkzeug, zustand in (
                ("Read", "tool_read"), ("NotebookRead", "tool_read"),
                ("Glob", "tool_search"), ("Grep", "tool_search"),
                ("Bash", "tool_bash"), ("PowerShell", "tool_bash"),
                ("WebFetch", "tool_web"), ("WebSearch", "tool_web"),
                ("Agent", "subagent"), ("Task", "subagent")):
            with self.subTest(werkzeug=werkzeug):
                self.assertEqual(app.werkzeug_zustand(werkzeug), zustand)

    def test_case_does_not_matter(self):
        self.assertEqual(app.werkzeug_zustand("bash"),
                         app.werkzeug_zustand("Bash"))

    def test_mcp_tools_count_as_network(self):
        # Ein MCP-Werkzeug spricht mit einem Server; welcher, wissen wir nicht.
        self.assertEqual(app.werkzeug_zustand("mcp__irgendwas__tu_was"),
                         "tool_web")

    def test_short_tools_have_no_phase_of_their_own(self):
        # Edit und Write sind vorbei, bevor ein Bild steht - die sollen die
        # laufende Animation nicht unterbrechen.
        for werkzeug in ("Edit", "Write", "TaskCreate", "", None):
            with self.subTest(werkzeug=werkzeug):
                self.assertIsNone(app.werkzeug_zustand(werkzeug))


class AnimationenTests(unittest.TestCase):
    def test_every_state_has_a_picture(self):
        fehlend = [z for z, anim in app.BUDDY_STATE_MAP.items()
                   if anim not in app.BUDDY_ANIMS]
        self.assertEqual(fehlend, [])

    def test_every_tool_phase_has_a_picture(self):
        for zustand in set(app.WERKZEUG_ZUSTAND.values()):
            with self.subTest(zustand=zustand):
                self.assertIn(zustand, app.BUDDY_STATE_MAP)
                self.assertIn(app.BUDDY_STATE_MAP[zustand], app.BUDDY_ANIMS)

    def test_every_hook_state_has_a_picture(self):
        for zustand in set(app.HOOK_ZUSTAND.values()):
            if zustand is None:
                continue
            with self.subTest(zustand=zustand):
                self.assertIn(zustand, app.BUDDY_STATE_MAP)
                self.assertIn(app.BUDDY_STATE_MAP[zustand], app.BUDDY_ANIMS)

    def test_states_do_not_share_an_animation(self):
        """Zwei Zustaende auf demselben Bild waeren nicht unterscheidbar.

        Die Rotations-Alternativen sind absichtlich doppelt belegt, die
        bleiben aussen vor.
        """
        ohne_alt = {z: a for z, a in app.BUDDY_STATE_MAP.items()
                    if not z.endswith("_alt")}
        doppelt = {a for a in ohne_alt.values()
                   if list(ohne_alt.values()).count(a) > 1}
        self.assertEqual(doppelt, set())

    def test_the_seven_new_animations_are_there(self):
        for name in ("error", "compact", "subagent", "tool read",
                     "tool search", "tool bash", "tool web"):
            with self.subTest(anim=name):
                self.assertIn(name, app.BUDDY_ANIMS)
                self.assertGreater(len(app.BUDDY_ANIMS[name]["frames"]), 1)



class ProtokollTests(unittest.TestCase):
    """Durch den echten Parser, nicht an ihm vorbei."""

    def setUp(self):
        import tempfile
        self.dir = tempfile.mkdtemp()
        self.projekt = os.path.join(self.dir, "projekt")
        os.makedirs(self.projekt)

    def schreibe(self, zeilen, sid="11111111-2222-3333-4444-555555555555"):
        pfad = os.path.join(self.projekt, sid + ".jsonl")
        with open(pfad, "w", encoding="utf-8") as fh:
            for z in zeilen:
                fh.write(json.dumps(z) + "\n")
        return pfad

    def assistent(self, bloecke):
        return {"type": "assistant", "timestamp": "2026-11-20T12:00:00.000Z",
                "message": {"role": "assistant", "content": bloecke}}

    def test_a_running_tool_is_reported_with_its_name(self):
        self.schreibe([
            {"type": "user", "message": {"role": "user", "content": "such mal"}},
            self.assistent([{"type": "tool_use", "id": "t1", "name": "Grep",
                             "input": {"pattern": "x"}}]),
        ])
        st = app._latest_jsonl_status(self.dir)
        self.assertTrue(st["has_pending_tool"])
        self.assertEqual(st["last_tool_name"], "Grep")
        self.assertEqual(app.werkzeug_zustand(st["last_tool_name"]),
                         "tool_search")

    def test_a_finished_tool_is_not_pending_any_more(self):
        self.schreibe([
            {"type": "user", "message": {"role": "user", "content": "such mal"}},
            self.assistent([{"type": "tool_use", "id": "t1", "name": "Grep",
                             "input": {"pattern": "x"}}]),
            {"type": "user", "message": {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "t1", "content": "ok"}]}},
        ])
        self.assertFalse(app._latest_jsonl_status(self.dir)["has_pending_tool"])

    def test_a_fresh_hook_message_reaches_the_status(self):
        from unittest.mock import patch
        import tempfile
        sid = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
        self.schreibe([{"type": "user",
                        "message": {"role": "user", "content": "los"}}], sid)
        hookdir = tempfile.mkdtemp()
        with open(os.path.join(hookdir, sid + ".json"), "w",
                  encoding="utf-8") as fh:
            json.dump(app.hook_eintrag({"hook_event_name": "PreCompact"}), fh)
        with patch.object(app, "HOOK_DIR", hookdir):
            st = app._latest_jsonl_status(self.dir)
        self.assertEqual(st["hook_zustand"], "compact")

    def test_a_stale_hook_message_is_ignored(self):
        from unittest.mock import patch
        import tempfile
        sid = "aaaaaaaa-bbbb-cccc-dddd-ffffffffffff"
        self.schreibe([{"type": "user",
                        "message": {"role": "user", "content": "los"}}], sid)
        hookdir = tempfile.mkdtemp()
        eintrag = app.hook_eintrag({"hook_event_name": "StopFailure"})
        eintrag["zeit"] = app.time.time() - app.HOOK_FRESH_S - 60
        with open(os.path.join(hookdir, sid + ".json"), "w",
                  encoding="utf-8") as fh:
            json.dump(eintrag, fh)
        with patch.object(app, "HOOK_DIR", hookdir):
            self.assertIsNone(app._latest_jsonl_status(self.dir)["hook_zustand"])


if __name__ == "__main__":
    unittest.main()
