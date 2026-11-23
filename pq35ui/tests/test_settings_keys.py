"""Settings read in several places must agree on their default.

A setting is read as settings.get("key", default) wherever it's needed. If two places use different
defaults, the app behaves differently depending on which runs first (a fresh install shows one
value on one page and another elsewhere). This scans the source, so a new mismatch fails here.

Run:  .venv/bin/python -m unittest pq35ui.tests.test_settings_keys   (no Qt needed)"""
import re
import unittest
from collections import defaultdict
from pathlib import Path

APP = Path(__file__).resolve().parents[1] / "app"
GET = re.compile(r'settings\.get\(\s*"([^"]+)"\s*(?:,\s*([^)]*?))?\s*\)')


def defaults():
    """{key: {default text: [file:line, ...]}} for every literal settings.get in the app."""
    found = defaultdict(lambda: defaultdict(list))
    for path in sorted(APP.glob("*.py")):
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            for key, default in GET.findall(line):
                found[key][(default or "None").strip()].append(f"{path.name}:{n}")
    return found


class SettingsDefaults(unittest.TestCase):
    def test_one_default_per_setting(self):
        bad = []
        for key, by_default in sorted(defaults().items()):
            # "None" (no default) next to a real one is fine when the caller handles None itself,
            # so only real defaults are compared
            real = {d: where for d, where in by_default.items() if d != "None"}
            if len(real) > 1:
                bad.append(f"{key}: " + "; ".join(f"{d} at {', '.join(w)}" for d, w in real.items()))
        self.assertEqual(bad, [], "settings read with different defaults")

    def test_scanner_finds_settings(self):
        self.assertIn("theme", defaults())  # the scan itself works


if __name__ == "__main__":
    unittest.main()
