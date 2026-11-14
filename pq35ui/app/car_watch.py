"""Knows which car is plugged in: reads its VIN quietly as soon as an interface is there (worker
"read_vin": a second, read-only, no busy state, skipped while anything else uses the module), keeps
trying until a car answers, then checks every so often so another car, or none, is noticed.

The VIN goes to AppState.set_vehicle (car_changed fires); Cars fills in the car's profile, Projects
opens its active project, and recordings are tagged with it. Two missed checks in a row: the car
is gone (AppState.car_gone) and recordings carry no car until one answers again."""
from PySide6.QtCore import QObject, QTimer

LOOK_S = 6        # no car yet: try this often
CHECK_S = 30      # a car is known: check it's still the same this often
MISSES_GONE = 2   # this many unanswered checks in a row: the car is gone


class CarWatch(QObject):
    def __init__(self, state, parent=None):
        super().__init__(parent)
        self.state = state
        self.pending = False
        self.misses = 0
        self.waited = 0.0
        self._adapter_seen = None
        self.timer = QTimer(self)
        self.timer.setInterval(1000)
        self.timer.timeout.connect(self._tick)
        self.timer.start()
        state.changed.connect(self._state_changed)
        QTimer.singleShot(500, self.check)

    def _state_changed(self):
        key = self._adapter_key()
        if key != self._adapter_seen:  # another (or a first) interface: look for the car now
            self._adapter_seen = key
            self.misses = 0
            QTimer.singleShot(300, self.check)

    def _adapter_key(self):
        a = self.state.adapter or {}
        return tuple(a.get(k) for k in ("adapter", "dll", "channel", "serial")) if a else None

    def _tick(self):
        self.waited += 1
        due = CHECK_S if self.state.vin_live else LOOK_S
        if self.waited >= due:
            self.check()

    def check(self):
        """Read the VIN now (if nothing else is using the module)."""
        state = self.state
        self.waited = 0
        if self.pending or not state.adapter or state.busy:
            return
        try:
            client = state.pool.for_adapter(state.adapter)
        except RuntimeError:
            return
        adapter = {k: v for k, v in state.adapter.items() if k in ("adapter", "dll", "channel", "serial", "bus")}
        self.pending = True
        client.call("read_vin", {"adapter": adapter}, self._result, self._failed)

    def _result(self, result):
        self.pending = False
        result = result or {}
        if result.get("busy"):
            return  # the module was in use: try again next time
        vin = result.get("vin")
        if vin:
            self.misses = 0
            self.state.set_vehicle(vin, result.get("source", ""))
        elif self.state.vin_live:
            self.misses += 1
            if self.misses >= MISSES_GONE:
                self.state.car_gone()

    def _failed(self, _error):
        self.pending = False
        if self.state.vin_live:
            self.misses += 1
            if self.misses >= MISSES_GONE:
                self.state.car_gone()


def watch(state, parent=None):
    """The app's one CarWatch."""
    w = getattr(state, "_car_watch", None)
    if w is None:
        w = state._car_watch = CarWatch(state, parent)
    return w
