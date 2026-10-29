"""Buddy detection regressions. Run: python -m unittest discover -s tests -v."""

import unittest
from unittest.mock import patch

import claude_sessions as app


STORE_DESKTOP = (
    r"C:\Program Files\WindowsApps\Claude_2.42304.0.0_x64__pzs8sxrjxfjjc"
    r"\app\Claude.exe"
)
LEGACY_DESKTOP = r"C:\Users\test\AppData\Local\AnthropicClaude\app-1.0\claude.exe"
NATIVE_CLI = r"C:\Users\test\.local\bin\claude.exe"


class ClaudeDetectionTests(unittest.TestCase):
    def setUp(self):
        self.cache = patch.object(
            app, "_CLAUDE_CACHE", {"t": 0.0, "active": False, "keys": frozenset()}
        )
        self.cache.start()
        self.addCleanup(self.cache.stop)

    def detect(self, processes=(), windows=()):
        """Windows fixtures are (handle, title, owning executable)."""
        owners = {hwnd: owner for hwnd, _title, owner in windows}
        with patch.object(app, "_win_process_names_with_path", return_value=processes), \
                patch.object(app, "_win_list_windows_hwnd", return_value=[
                    (hwnd, title) for hwnd, title, _owner in windows
                ]), \
                patch.object(app, "_win_hwnd_process", side_effect=owners.get):
            return app._claude_context_active()

    def test_store_desktop_background_processes_do_not_activate_buddy(self):
        self.assertFalse(self.detect([
            ("claude.exe", STORE_DESKTOP, 101),
            ("claude.exe", STORE_DESKTOP, 102),
        ]))
        self.assertEqual(app._CLAUDE_CACHE["keys"], frozenset())

    def test_store_path_is_case_and_separator_insensitive(self):
        self.assertFalse(self.detect([
            ("claude.exe", STORE_DESKTOP.upper().replace("\\", "/"), 101),
        ]))

    def test_other_store_package_versions_are_excluded(self):
        self.assertFalse(self.detect([
            ("claude.exe", r"D:\WindowsApps\Claude_9.0.0.0_arm64__publisher"
             r"\app\Claude.exe", 101),
        ]))

    def test_legacy_desktop_background_process_does_not_activate_buddy(self):
        self.assertFalse(self.detect([("claude.exe", LEGACY_DESKTOP, 101)]))

    def test_desktop_window_does_not_bypass_process_filter(self):
        for path in (STORE_DESKTOP, LEGACY_DESKTOP):
            with self.subTest(path=path):
                app._CLAUDE_CACHE["t"] = 0.0
                self.assertFalse(self.detect(
                    [("claude.exe", path, 101)],
                    [(201, "Claude", "claude.exe")],
                ))

    def test_desktop_window_does_not_require_process_snapshot(self):
        self.assertFalse(self.detect(windows=[(201, "Claude", "claude.exe")]))

    def test_unreadable_claude_path_is_not_assumed_to_be_cli(self):
        self.assertFalse(self.detect(
            [("claude.exe", "", 101)], [(201, "Claude", "claude.exe")]
        ))

    def test_unknown_window_owner_is_not_assumed_to_be_cli(self):
        self.assertFalse(self.detect(windows=[(201, "Claude", "")]))

    def test_native_cli_activates_without_a_window(self):
        self.assertTrue(self.detect([("claude.exe", NATIVE_CLI, 103)]))
        self.assertEqual(app._CLAUDE_CACHE["keys"], frozenset({("pid", 103)}))

    def test_cli_inside_store_package_is_not_the_desktop_executable(self):
        bundled_cli = STORE_DESKTOP.rsplit("\\", 1)[0] + r"\resources\claude-code\claude.exe"
        self.assertTrue(self.detect([("claude.exe", bundled_cli, 103)]))

    def test_similar_folder_name_does_not_hide_cli(self):
        self.assertTrue(self.detect([
            ("claude.exe", r"C:\AnthropicClaude-tools\bin\claude.exe", 103),
        ]))

    def test_cli_and_desktop_together_only_track_cli(self):
        self.assertTrue(self.detect([
            ("claude.exe", STORE_DESKTOP, 101),
            ("claude.exe", NATIVE_CLI, 103),
        ], [(201, "Claude", "claude.exe")]))
        self.assertEqual(app._CLAUDE_CACHE["keys"], frozenset({("pid", 103)}))

    def test_cli_exit_hides_buddy_while_desktop_remains(self):
        with patch.object(app.time, "time", return_value=100.0):
            self.assertTrue(self.detect([
                ("claude.exe", STORE_DESKTOP, 101),
                ("claude.exe", NATIVE_CLI, 103),
            ]))
        with patch.object(app.time, "time", return_value=103.0):
            self.assertFalse(self.detect(
                [("claude.exe", STORE_DESKTOP, 101)],
                [(201, "Claude", "claude.exe")],
            ))
        self.assertEqual(app._CLAUDE_CACHE["keys"], frozenset())

    def test_npm_cli_terminal_detection_is_preserved(self):
        self.assertTrue(self.detect(
            [("node.exe", "", 104), ("claude.exe", STORE_DESKTOP, 101)],
            [(202, "Claude Code - project", "windowsterminal.exe"),
             (201, "Claude", "claude.exe")],
        ))
        self.assertEqual(app._CLAUDE_CACHE["keys"], frozenset({("win", 202)}))

    def test_browser_and_session_browser_titles_are_ignored(self):
        self.assertFalse(self.detect(windows=[
            (203, "Claude - Google Chrome", "chrome.exe"),
            (204, "Claude Session Browser", "claudesessionbrowser.exe"),
        ]))


if __name__ == "__main__":
    unittest.main()
