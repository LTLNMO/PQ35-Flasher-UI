"""Logger smart presets: every set stays within LoggerProfile.MAX_GROUPS measuring groups.

Run:  .venv/bin/python -m unittest pq35ui.tests.test_smart_presets   (no Qt window needed)"""
import unittest

from pq35ui.app.smart_presets import LoggerProfile, channels_of, rule_layouts


class LoggerFit(unittest.TestCase):
    def setUp(self):
        self.p = LoggerProfile()
        # rpm in group 1; boost + N75 in 115; load in 2; six unrelated "load" channels in their own groups
        self.p.groups = {"Engine speed (rpm)": 1, "Boost pressure (actual) (mbar)": 115,
                         "Boost pressure (specified) (mbar)": 115, "Boost control valve duty (N75) (%)": 115,
                         "Engine load (actual) (%)": 2, "Engine load (specified) (%)": 2}
        self.p.groups.update({f"Odd load {i} (%)": 200 + i for i in range(6)})

    def test_caps_groups_keeping_the_first_ones(self):
        layout = {"name": "Boost", "charts": [
            {"title": "Engine speed", "channels": ["Engine speed (rpm)"]},
            {"title": "Boost", "channels": ["Boost pressure (actual) (mbar)", "Boost pressure (specified) (mbar)"]},
            {"title": "Load", "channels": [f"Odd load {i} (%)" for i in range(6)] + ["Engine load (actual) (%)"]},
        ]}
        out = self.p.fit([layout])
        groups = {self.p.groups[n] for n in channels_of(out[0])}
        self.assertLessEqual(len(groups), LoggerProfile.MAX_GROUPS)
        self.assertIn(1, groups)
        self.assertIn(115, groups)

    def test_free_channels_in_a_read_group_stay(self):
        layout = {"name": "Boost", "charts": [{"title": "a", "channels": [
            "Engine speed (rpm)", "Odd load 0 (%)", "Odd load 1 (%)", "Odd load 2 (%)",
            "Engine load (actual) (%)", "Engine speed (rpm)"]}]}
        out = self.p.fit([layout])
        self.assertNotIn("Engine load (actual) (%)", channels_of(out[0]))  # a 5th group: dropped
        self.assertIn("Engine speed (rpm)", channels_of(out[0]))

    def test_rules_over_a_whole_module_fit(self):
        for l in self.p.fit(rule_layouts(list(self.p.groups))):
            self.assertLessEqual(len({self.p.groups[n] for n in channels_of(l)}), LoggerProfile.MAX_GROUPS)


if __name__ == "__main__":
    unittest.main()


class FaultPresets(unittest.TestCase):
    HEADERS = ["Engine speed (rpm)", "Boost pressure (actual) (mbar)", "Boost pressure (specified) (mbar)",
               "Boost control valve duty (N75) (%)", "Engine load (actual) (%)", "Lambda (actual)",
               "Lambda (specified)", "Lambda control bank 1 (%)", "Coolant temperature (°C)"]

    def test_faults_lead_with_matching_sets(self):
        from pq35ui.app.smart_presets import fault_rule_layouts
        out = fault_rule_layouts(self.HEADERS, ["P0299: Turbocharger underboost", "P0171: System too lean bank 1"])
        self.assertEqual([l["fault"] for l in out], ["P0299", "P0171"])
        self.assertIn("Boost pressure (actual) (mbar)", channels_of(out[0]))
        self.assertIn("Lambda (actual)", channels_of(out[1]))

    def test_prompt_mentions_faults_and_requests(self):
        from pq35ui.app.smart_presets import extras
        self.assertIn("P0299", extras(["P0299: underboost"]))
        asked = extras(["P0299: underboost"], want="check for a boost leak")
        self.assertIn("EXACTLY 1 layout", asked)
        self.assertNotIn("P0299", asked)  # a request is about the request only
