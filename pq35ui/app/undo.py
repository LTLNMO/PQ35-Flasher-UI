"""Snapshot-based undo/redo. A page describes its state as a plain dict; every
user change commits a new snapshot, undo/redo walk back and forth."""


class SnapshotHistory:
    LIMIT = 50

    def __init__(self):
        self.current = None
        self.past = []
        self.future = []

    def reset(self, snapshot):
        self.current = snapshot
        self.past.clear()
        self.future.clear()

    def commit(self, snapshot) -> bool:
        """Record snapshot if it differs from the current one. True if recorded."""
        if snapshot == self.current:
            return False
        if self.current is not None:
            self.past.append(self.current)
            del self.past[:-self.LIMIT]
        self.current = snapshot
        self.future.clear()
        return True

    def can_undo(self):
        return bool(self.past)

    def can_redo(self):
        return bool(self.future)

    def undo(self):
        if not self.past:
            return None
        self.future.append(self.current)
        self.current = self.past.pop()
        return self.current

    def redo(self):
        if not self.future:
            return None
        self.past.append(self.current)
        self.current = self.future.pop()
        return self.current
