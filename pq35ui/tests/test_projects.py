"""Projects ↔ Cars: auto-filing new recordings into the car's active project, and Recent logs
keeping what projects link to. Runs in throwaway folders (PQ35_DATA_DIR / PQ35_USER_DIR)."""
import os
import tempfile
import time
import unittest


class ProjectLinks(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = {k: os.environ.get(k) for k in ("PQ35_DATA_DIR", "PQ35_USER_DIR")}
        os.environ["PQ35_DATA_DIR"] = os.path.join(self.tmp.name, "data")
        os.environ["PQ35_USER_DIR"] = os.path.join(self.tmp.name, "user")
        from pq35ui.app import cars, projects, recent_logs
        from pq35ui.tests.test_dyno import FakeSettings
        self.cars, self.projects, self.recent = cars, projects, recent_logs
        self.settings = FakeSettings()
        self.vin = "WAUMFAFM7BA056804"
        cars.save(self.settings, self.vin, cars.guess(self.vin))
        self.saved = (list(recent_logs._finished_listeners), list(recent_logs._keepers))

    def tearDown(self):
        self.recent._finished_listeners[:], self.recent._keepers[:] = self.saved
        for k, v in self.env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self.tmp.cleanup()

    def record(self, vin, mode="continuous", rows=10):
        path = self.recent.new_log("engine", vin=vin, mode=mode)
        path.write_text("Time,x\n0,1\n")
        self.recent.finish(path, rows, 1.0)
        return path

    def test_new_recordings_go_to_the_active_project(self):
        p = self.projects.Project.create("A3 Stage 1")
        p.data["car_id"] = self.vin
        p.save()
        self.projects.set_active(self.settings, self.vin, p)
        state = type("S", (), {"settings": self.settings})()
        self.projects.attach(state)
        log = self.record(self.vin)
        dyno_session = self.record(self.vin, mode="dyno")
        other_car = self.record("WVWZZZ1KZBW000123")
        logs = self.projects.Project(p.root).data["logs"]
        self.assertEqual(logs, [str(log), str(dyno_session)])
        self.assertNotIn(str(other_car), logs)
        self.assertEqual(self.projects.project_for_log(log).name, "A3 Stage 1")

    def test_no_active_project_no_link(self):
        p = self.projects.Project.create("Old")
        p.data["car_id"] = self.vin
        p.save()
        self.projects.attach(type("S", (), {"settings": self.settings})())
        self.record(self.vin)
        self.assertEqual(self.projects.Project(p.root).data["logs"], [])

    def test_recordings_never_deleted_on_their_own(self):
        """Since Nov 2026 recordings live in Datalogs and only Discard… removes one."""
        self.projects.attach(type("S", (), {"settings": self.settings})())
        keep = self.record(self.vin)
        drop = self.record(self.vin)
        p = self.projects.Project.create("Keeper")
        p.data["logs"].append(str(keep))
        p.save()
        data = self.recent._load()
        for name in data:  # both "old"
            data[name]["started"] = time.time() - 40 * 86400
        self.recent._save(data)
        self.recent.prune(keep=0, days=14)
        self.assertTrue(keep.exists())
        self.assertTrue(drop.exists())
        self.assertEqual(keep.parent.name, "Datalogs")
        self.recent.discard(drop)
        self.assertFalse(drop.exists())


if __name__ == "__main__":
    unittest.main()
