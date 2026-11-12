"""Usage polling across plan shapes. Run: python -m unittest discover -s tests -v.

Anthropic dropped the 5-hour window for some plans: those accounts return only
7d-* rate-limit headers. The poller has to say which windows exist instead of
reporting a 5-hour window stuck at 0%.
"""

import time
import unittest
from unittest.mock import patch

import claude_sessions as app
import clawdmeter


class _Resp:
    def __init__(self, headers):
        self.headers = headers

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def poll(headers):
    with patch.object(clawdmeter.urllib.request, "urlopen",
                      return_value=_Resp(headers)):
        return clawdmeter.poll_usage_meta("fake-token")


class PollWindowsTests(unittest.TestCase):
    def setUp(self):
        self.now = time.time()

    def test_weekly_only_account(self):
        # Exactly the header set a weekly-only account returned on 2026-11-12.
        reset = self.now + 4 * 86400
        payload, meta = poll({
            "anthropic-ratelimit-unified-7d-utilization": "0.1",
            "anthropic-ratelimit-unified-7d-reset": str(reset),
            "anthropic-ratelimit-unified-7d-status": "allowed",
            "anthropic-ratelimit-unified-status": "allowed",
            "anthropic-ratelimit-unified-reset": str(reset),
            "anthropic-ratelimit-unified-representative-claim": "seven_day",
            "anthropic-ratelimit-unified-overage-status": "rejected",
        })
        self.assertEqual(payload["lim"], "w")
        self.assertEqual(payload["acct"], "pro")
        self.assertEqual(payload["w"], 10)
        self.assertEqual(payload["wr"], 4 * 1440)
        self.assertEqual(payload["s"], 0)
        self.assertEqual(payload["sr"], -1)      # no such window
        self.assertEqual(payload["st"], "allowed")
        self.assertNotIn("sx", payload)
        self.assertEqual(meta["windows"], "w")
        self.assertEqual(meta["session_reset_at"], 0.0)
        self.assertEqual(meta["weekly_reset_at"], reset)
        self.assertEqual(meta["weekly_pct"], 10)

    def test_both_windows_payload_is_unchanged(self):
        payload, meta = poll({
            "anthropic-ratelimit-unified-5h-utilization": "0.42",
            "anthropic-ratelimit-unified-5h-reset": str(self.now + 3600),
            "anthropic-ratelimit-unified-5h-status": "allowed_warning",
            "anthropic-ratelimit-unified-7d-utilization": "0.18",
            "anthropic-ratelimit-unified-7d-reset": str(self.now + 86400),
            "anthropic-ratelimit-unified-status": "allowed",
        })
        self.assertNotIn("lim", payload)         # old firmware sees no change
        self.assertEqual((payload["s"], payload["sr"]), (42, 60))
        self.assertEqual((payload["w"], payload["wr"]), (18, 1440))
        self.assertEqual(payload["st"], "allowed_warning")   # 5h status wins
        self.assertEqual(meta["windows"], "sw")

    def test_session_only_account(self):
        payload, meta = poll({
            "anthropic-ratelimit-unified-5h-utilization": "0.64",
            "anthropic-ratelimit-unified-5h-reset": str(self.now + 5700),
            "anthropic-ratelimit-unified-5h-status": "allowed",
        })
        self.assertEqual(payload["lim"], "s")
        self.assertEqual(payload["s"], 64)
        self.assertEqual(payload["wr"], -1)
        self.assertEqual(meta["windows"], "s")
        self.assertEqual(meta["weekly_reset_at"], 0.0)

    def test_enterprise_account(self):
        payload, meta = poll({
            "anthropic-ratelimit-unified-overage-utilization": "0.31",
            "anthropic-ratelimit-unified-overage-reset": str(self.now + 86400),
            "anthropic-ratelimit-unified-status": "allowed",
        })
        self.assertEqual(payload["acct"], "ent")
        self.assertEqual(payload["s"], 31)
        self.assertNotIn("lim", payload)

    def test_no_ratelimit_headers_is_a_failed_poll(self):
        payload, meta = poll({"content-type": "application/json"})
        self.assertIsNone(payload)
        self.assertEqual(meta, {})


class _Tray:
    def __init__(self):
        self.messages = []
        self.icon = self

    def notify(self, text, title):
        self.messages.append(text)


class _Buddy:
    def __init__(self):
        self.resets = 0

    def _notify_limit_reset(self):
        self.resets += 1


def make_api(**settings):
    """An Api with just the state on_usage_meta / limit_state touch."""
    api = app.Api.__new__(app.Api)
    api.settings = {"limit_warn_pct": 90, "notify_limit_near": True}
    api.settings.update(settings)
    api._tray = _Tray()
    api.buddy = _Buddy()
    return api


class LimitWatchTests(unittest.TestCase):
    def setUp(self):
        self.save = patch.object(app, "save_json")
        self.save.start()
        self.addCleanup(self.save.stop)
        self.now = time.time()

    def meta(self, **kw):
        base = {"session_pct": 0, "session_reset_at": 0.0, "weekly_pct": 0,
                "weekly_reset_at": 0.0, "windows": "sw", "at": self.now}
        base.update(kw)
        return base

    def test_weekly_only_account_gets_a_warning(self):
        api = make_api()
        reset = self.now + 2 * 86400 + 3 * 3600
        api.on_usage_meta(self.meta(windows="w", weekly_pct=93,
                                    weekly_reset_at=reset))
        self.assertEqual(len(api._tray.messages), 1)
        self.assertIn("93%", api._tray.messages[0])
        self.assertIn("2 d", api._tray.messages[0])
        self.assertEqual(api.settings["limit_window_kind"], "w")
        # Once per window, not once per poll.
        api.on_usage_meta(self.meta(windows="w", weekly_pct=94,
                                    weekly_reset_at=reset))
        self.assertEqual(len(api._tray.messages), 1)

    def test_weekly_only_below_threshold_stays_quiet(self):
        api = make_api()
        api.on_usage_meta(self.meta(windows="w", weekly_pct=10,
                                    weekly_reset_at=self.now + 86400))
        self.assertEqual(api._tray.messages, [])

    def test_five_hour_window_still_drives_the_warning_when_present(self):
        api = make_api()
        api.on_usage_meta(self.meta(session_pct=95,
                                    session_reset_at=self.now + 1800,
                                    weekly_pct=20,
                                    weekly_reset_at=self.now + 86400))
        self.assertEqual(len(api._tray.messages), 1)
        self.assertIn("95%", api._tray.messages[0])
        # "s" is the default, so it is only written once it has to change.
        self.assertEqual(api.settings.get("limit_window_kind", "s"), "s")

    def test_weekly_reset_after_a_hot_week_announces_the_reset(self):
        api = make_api()
        old = self.now + 3600
        api.on_usage_meta(self.meta(windows="w", weekly_pct=97,
                                    weekly_reset_at=old))
        api.on_usage_meta(self.meta(windows="w", weekly_pct=0,
                                    weekly_reset_at=old + 7 * 86400))
        self.assertEqual(api.buddy.resets, 1)

    def test_losing_the_five_hour_window_is_not_a_reset(self):
        # Hot 5-hour window, then the plan stops reporting one. The weekly
        # reset time differs from the stored 5h one, but nothing ran out.
        api = make_api()
        api.on_usage_meta(self.meta(session_pct=96,
                                    session_reset_at=self.now + 1800,
                                    weekly_pct=30,
                                    weekly_reset_at=self.now + 86400))
        api.on_usage_meta(self.meta(windows="w", weekly_pct=30,
                                    weekly_reset_at=self.now + 86400))
        self.assertEqual(api.buddy.resets, 0)

    def test_limit_state_reports_windows_and_weekly_hit(self):
        api = make_api()
        api.on_usage_meta(self.meta(windows="w", weekly_pct=100,
                                    weekly_reset_at=self.now + 86400))
        st = api.limit_state()
        self.assertEqual(st["windows"], "w")
        self.assertTrue(st["hit"])
        self.assertEqual(st["wpct"], 100)
        self.assertEqual(st["reset_at"], 0)

    def test_limit_state_hit_follows_five_hour_window_when_present(self):
        api = make_api()
        api.on_usage_meta(self.meta(session_pct=40,
                                    session_reset_at=self.now + 1800,
                                    weekly_pct=100,
                                    weekly_reset_at=self.now + 86400))
        st = api.limit_state()
        self.assertEqual(st["windows"], "sw")
        self.assertFalse(st["hit"])


if __name__ == "__main__":
    unittest.main()
