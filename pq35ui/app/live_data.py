"""Live View's per-channel history, without Qt: adding a reading (bounded, trimmed in chunks) and
the points inside a graph's time window. Tested directly (tests/test_live_data.py)."""
import bisect

MAX_POINTS = 6000  # readings kept per channel (a few minutes at full speed)
TRIM_SLACK = 1000  # a channel may run this far past MAX_POINTS before its oldest points go


def push(ch, t, value):
    """Add one reading to channel `ch` ({"x": [...], "y": [...]}). Trimmed in one go now and then,
    in place: copying all MAX_POINTS on every reading once full was most of a long session's work."""
    ch["x"].append(t)
    ch["y"].append(value)
    if len(ch["x"]) > MAX_POINTS + TRIM_SLACK:
        del ch["x"][:-MAX_POINTS]
        del ch["y"][:-MAX_POINTS]


def window(ch, now, seconds):
    """(times, values) inside the last `seconds` up to `now`, plus one point before, so a line runs
    in from the graph's left edge."""
    xs = ch["x"]
    start = max(bisect.bisect_left(xs, now - seconds) - 1, 0)
    return xs[start:], ch["y"][start:]
