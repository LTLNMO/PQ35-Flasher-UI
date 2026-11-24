"""channel_knowledge: every note points at a real catalog channel under its real name, and log
headers (with units) find their notes. Guards the notes the AI and the tooltips rely on.

Run:  PYTHONPATH=.:flasher .venv/bin/python -m unittest pq35ui.tests.test_channel_knowledge"""
import unittest

from pq35ui.app import channel_knowledge as K


class Notes(unittest.TestCase):
    def test_notes_match_the_catalog(self):
        try:
            from measuring_catalog._data import VALUES
        except ImportError:
            from flasher.measuring_catalog._data import VALUES
        names = {v[0]: v[1] for v in VALUES}
        bad = [f"{cid}: not in the catalog" for cid in K.NOTES if cid not in names]
        bad += [f"{cid}: note says {K.NOTES[cid][0]!r}, catalog says {names[cid]!r}"
                for cid in K.NOTES if cid in names and names[cid] != K.NOTES[cid][0]]
        self.assertEqual(bad, [])

    def test_log_headers_find_their_notes(self):
        for header in ("Engine load (actual) (%)", "Knock sensor voltage cylinder 3 (V)",
                       "Lambda control bank 1 (actual)", "Timing retard cylinder 4 (°)",
                       "Boost pressure (actual absolute) (mbar)"):
            self.assertIsNotNone(K.note_for(name=header), header)
        self.assertIsNone(K.note_for(name="Gear (actual)"))
        self.assertIn("640-800", K.note_for(name="Engine speed (rpm)"))

    def test_notes_text_once_per_note(self):
        text = K.notes_text([f"Timing retard cylinder {n} (°)" for n in range(1, 5)])
        self.assertEqual(text.count("\n- "), 1)  # the four cylinders share one note


if __name__ == "__main__":
    unittest.main()
