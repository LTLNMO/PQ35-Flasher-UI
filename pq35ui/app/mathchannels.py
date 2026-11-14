"""Math channels: user formulas over other channels, e.g.

    Boost (psi) = ([Boost pressure actual] - 1013) * 0.0145
    AFR         = [Lambda actual] * 14.7

Channels are referenced by name in square brackets. Only arithmetic, comparisons
and a few math functions are allowed (no Python builtins, no attribute access).
The same formula works on single numbers (Live View) and numpy arrays (Analyze).

Stored globally in settings under "math_channels" as [{name, unit, expr}]."""
import ast
import re

import numpy as np
from PySide6.QtWidgets import (
    QComboBox, QDialogButtonBox, QFormLayout, QHBoxLayout, QLabel, QLineEdit,
    QListWidget, QListWidgetItem, QPushButton, QVBoxLayout,
)

from .widgets import muted
from .popups import MessageBox, OverlayDialog

SETTINGS_KEY = "math_channels"
_REF = re.compile(r"\[([^\[\]]+)\]")
FUNCTIONS = {
    "abs": np.abs, "min": np.minimum, "max": np.maximum, "sqrt": np.sqrt,
    "log": np.log, "log10": np.log10, "exp": np.exp, "round": np.round,
    "clip": np.clip, "where": np.where,
}
CONSTANTS = {"pi": np.pi, "e": np.e}
_ALLOWED = (
    ast.Expression, ast.BinOp, ast.UnaryOp, ast.Compare, ast.BoolOp, ast.Call, ast.Name,
    ast.Load, ast.Constant, ast.IfExp,
    ast.Add, ast.Sub, ast.Mult, ast.Div, ast.FloorDiv, ast.Mod, ast.Pow, ast.USub, ast.UAdd,
    ast.Lt, ast.LtE, ast.Gt, ast.GtE, ast.Eq, ast.NotEq, ast.And, ast.Or, ast.Not,
)


class Formula:
    """A compiled formula. refs: channel names it needs, in order."""

    def __init__(self, expr: str):
        self.expr = expr
        self.refs = []
        def sub(match):
            name = match.group(1).strip()
            if name not in self.refs:
                self.refs.append(name)
            return f"_v{self.refs.index(name)}"
        source = _REF.sub(sub, expr)
        try:
            tree = ast.parse(source, mode="eval")
        except SyntaxError as e:
            raise ValueError(f"Formula doesn't parse: {e.msg}") from None
        for node in ast.walk(tree):
            if not isinstance(node, _ALLOWED):
                raise ValueError(f"Not allowed in a formula: {type(node).__name__}")
            if isinstance(node, ast.Name) and not (
                    node.id.startswith("_v") or node.id in FUNCTIONS or node.id in CONSTANTS):
                raise ValueError(f"Unknown name “{node.id}”. Put channel names in [brackets].")
            if isinstance(node, ast.Call) and not (isinstance(node.func, ast.Name) and node.func.id in FUNCTIONS):
                raise ValueError("Only abs, min, max, sqrt, log, log10, exp, round, clip, where can be called.")
            if isinstance(node, ast.Constant):
                if isinstance(node.value, bool) or not isinstance(node.value, (int, float)):
                    raise ValueError("Only numbers are allowed as constants.")
                node.value = float(node.value)  # float math: huge powers overflow instead of hanging
        if not self.refs:
            raise ValueError("Use at least one channel, e.g. [Engine speed].")
        self.code = compile(tree, "<math channel>", "eval")

    def evaluate(self, values):
        """values: list matching self.refs (floats or numpy arrays)."""
        env = {"__builtins__": {}}
        env.update(FUNCTIONS)
        env.update(CONSTANTS)
        env.update({f"_v{i}": v for i, v in enumerate(values)})
        with np.errstate(all="ignore"):
            try:
                return eval(self.code, env)  # noqa: S307 - AST is whitelisted above
            except (ArithmeticError, ValueError, TypeError):
                return float("nan")


def load(settings):
    return [m for m in (settings.get(SETTINGS_KEY) or []) if m.get("name") and m.get("expr")]


def compiled(settings):
    """[(definition, Formula)] for the formulas that compile."""
    out = []
    for definition in load(settings):
        try:
            out.append((definition, Formula(definition["expr"])))
        except ValueError:
            continue
    return out


def strip_unit(header):
    match = re.match(r"^(.*?) \(([^()]*)\)$", header)
    return match.group(1) if match else header


class MathChannelsDialog(OverlayDialog):
    """List + editor for math channels. channel_names: what [refs] can use."""

    CARD_ICON = "function"  # header icon when shown as a card

    def __init__(self, settings, channel_names, parent=None):
        super().__init__(parent)
        self.settings = settings
        self.names = sorted(set(channel_names), key=str.lower)
        self.items = load(settings)
        self.setWindowTitle("Math channels")
        self.setMinimumWidth(620)
        layout = QVBoxLayout(self)
        layout.addWidget(muted("Make new channels from formulas. Reference channels by name in "
                               "[brackets]. Functions: abs, min, max, sqrt, log, exp, round, clip, where."))
        body = QHBoxLayout()
        left = QVBoxLayout()
        self.list = QListWidget()
        self.list.currentRowChanged.connect(self._select)
        left.addWidget(self.list, 1)
        row = QHBoxLayout()
        add = QPushButton("New")
        add.clicked.connect(self._new)
        row.addWidget(add)
        self.remove = QPushButton("Remove")
        self.remove.clicked.connect(self._remove)
        row.addWidget(self.remove)
        left.addLayout(row)
        body.addLayout(left, 1)

        form = QFormLayout()
        self.name = QLineEdit()
        self.name.setPlaceholderText("Boost (psi)")
        self.unit = QLineEdit()
        self.unit.setPlaceholderText("psi")
        self.expr = QLineEdit()
        self.expr.setPlaceholderText("([Boost pressure actual] - 1013) * 0.0145")
        self.insert = QComboBox()
        self.insert.addItem("Insert channel…", None)
        for n in self.names:
            self.insert.addItem(n, n)
        self.insert.activated.connect(self._insert)
        self.status = QLabel("")
        self.status.setWordWrap(True)
        form.addRow("Name", self.name)
        form.addRow("Unit", self.unit)
        form.addRow("Formula", self.expr)
        form.addRow("", self.insert)
        form.addRow("", self.status)
        for w in (self.name, self.unit, self.expr):
            w.textEdited.connect(self._edited)
        body.addLayout(form, 2)
        layout.addLayout(body, 1)

        examples = muted("Examples: AFR = [Lambda actual] * 14.7 · Boost error = [Boost pressure specified] "
                         "- [Boost pressure actual] · Over 6000 = where([Engine speed] > 6000, 1, 0)")
        examples.setWordWrap(True)
        layout.addWidget(examples)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self._refresh_list()
        if self.items:
            self.list.setCurrentRow(0)
        else:
            self._select(-1)

    def _refresh_list(self):
        row = self.list.currentRow()
        self.list.blockSignals(True)
        self.list.clear()
        for item in self.items:
            QListWidgetItem(item["name"] or "(unnamed)", self.list)
        self.list.blockSignals(False)
        if 0 <= row < len(self.items):
            self.list.setCurrentRow(row)
        self.remove.setEnabled(bool(self.items))

    def _select(self, row):
        on = 0 <= row < len(self.items)
        for w in (self.name, self.unit, self.expr, self.insert):
            w.setEnabled(on)
        item = self.items[row] if on else {"name": "", "unit": "", "expr": ""}
        self.name.setText(item["name"])
        self.unit.setText(item.get("unit", ""))
        self.expr.setText(item["expr"])
        self._validate()

    def _edited(self, *_):
        row = self.list.currentRow()
        if not 0 <= row < len(self.items):
            return
        self.items[row] = {"name": self.name.text().strip(), "unit": self.unit.text().strip(),
                           "expr": self.expr.text().strip()}
        self.list.item(row).setText(self.items[row]["name"] or "(unnamed)")
        self._validate()

    def _validate(self):
        text = self.expr.text().strip()
        if not text:
            self.status.setText("")
            return True
        try:
            formula = Formula(text)
        except ValueError as e:
            self.status.setText(f"<span style='color:{_c('err')}'>{e}</span>")
            return False
        missing = [r for r in formula.refs if r not in self.names]
        if missing and self.names:
            self.status.setText(f"<span style='color:{_c('warn')}'>Not on this module: "
                                + ", ".join(missing) + ". It'll be skipped where those channels don't exist.</span>")
        else:
            self.status.setText(f"<span style='color:{_c('ok')}'>Formula OK.</span>")
        return True

    def _insert(self, index):
        name = self.insert.itemData(index)
        self.insert.setCurrentIndex(0)
        if name:
            self.expr.insert(f"[{name}]")
            self._edited()
            self.expr.setFocus()

    def _new(self):
        self.items.append({"name": f"Math {len(self.items) + 1}", "unit": "", "expr": ""})
        self._refresh_list()
        self.list.setCurrentRow(len(self.items) - 1)
        self.expr.setFocus()

    def _remove(self):
        row = self.list.currentRow()
        if 0 <= row < len(self.items):
            self.items.pop(row)
            self._refresh_list()
            self._select(self.list.currentRow())

    def _save(self):
        names = set()
        for item in self.items:
            if not item["name"] or not item["expr"]:
                MessageBox.warning(self, "Math channels", "Every math channel needs a name and a formula.")
                return
            if item["name"] in names:
                MessageBox.warning(self, "Math channels", f"Two math channels are called “{item['name']}”.")
                return
            names.add(item["name"])
            try:
                Formula(item["expr"])
            except ValueError as e:
                MessageBox.warning(self, "Math channels", f"{item['name']}: {e}")
                return
        self.settings.set(SETTINGS_KEY, self.items)
        self.accept()


def _c(key):
    from . import theme
    return theme.colors()[key]
