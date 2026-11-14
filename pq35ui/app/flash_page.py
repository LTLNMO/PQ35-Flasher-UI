"""Flash: write a firmware image to the module (split out of pages.py)."""
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (QButtonGroup, QCheckBox, QFileDialog, QHBoxLayout, QPushButton,
                               QRadioButton, QVBoxLayout, QWidget)

from .storage import patched_dir, timestamp
from .widgets import Banner, Card, DropZone, FlowRow, HoldButton, ProgressPanel, kib, muted
from .popups import InputDialog, MessageBox
from .pages import BACKUP_FAMILIES, BCM, EPS, HALDEX, Page, convert_sgo_then


class FlashPage(Page):
    title = "Flash"

    def __init__(self, state, parent=None):
        super().__init__(state, parent)
        self.path = None
        self.loaded_for = None
        self.inspection = None
        self.plan = None
        self.gate = self.add(Banner())

        # Undo / redo for the image, its patches and the advanced options.
        # The "Before you flash" checklist is deliberately not part of it.
        from .undo import SnapshotHistory
        self.history = SnapshotHistory()
        self._restoring = False
        self._pending_option = None
        undo_bar = QWidget()
        undo_row = QHBoxLayout(undo_bar)
        undo_row.setContentsMargins(0, 0, 0, 0)
        undo_row.addStretch(1)
        self.undo_button = QPushButton("Undo")
        self.undo_button.setToolTip("Undo the last change to the image, patches or options (Ctrl+Z)")
        self.undo_button.clicked.connect(self.undo)
        undo_row.addWidget(self.undo_button)
        self.redo_button = QPushButton("Redo")
        self.redo_button.setToolTip("Redo (Ctrl+Shift+Z)")
        self.redo_button.clicked.connect(self.redo)
        undo_row.addWidget(self.redo_button)
        self.add(undo_bar)
        for keys, slot in ((QKeySequence.StandardKey.Undo, self.undo), (QKeySequence.StandardKey.Redo, self.redo)):
            shortcut = QShortcut(QKeySequence(keys), self)
            shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            shortcut.activated.connect(slot)

        # BCM only: a read-only check of the four gates a BCM flash's 10 85 needs
        # (supply voltage, attempt counter, two flags). Independent of the flash
        # gate below — run it any time a BCM is connected; nothing is written.
        self.precond = QWidget()
        precond_box = QVBoxLayout(self.precond)
        precond_box.setContentsMargins(0, 0, 0, 0)
        precond_row = QHBoxLayout()
        self.precond_button = QPushButton("Check programming conditions")
        self.precond_button.clicked.connect(self.check_preconditions)
        precond_row.addWidget(self.precond_button)
        precond_row.addStretch(1)
        precond_box.addLayout(precond_row)
        self.precond_banner = Banner()
        precond_box.addWidget(self.precond_banner)
        self.precond_note = muted("")
        precond_box.addWidget(self.precond_note)
        self.precond_note.hide()
        self.add(self.precond)
        self.precond.hide()

        file_card = self.add(Card("1. Choose the image"))
        self.drop = file_card.add(DropZone())
        self.drop.picked.connect(self.load)
        self.badges = file_card.add(FlowRow())
        self.file_banner = file_card.add(Banner())
        # EPS only: patch the chosen image in place. The patched copy is saved to
        # the Patched folder and flashed; the chosen file itself never changes.
        self.patch_source = None
        self.patch_info = None
        self.patch_row = QWidget()
        patch_box = QHBoxLayout(self.patch_row)
        patch_box.setContentsMargins(0, 0, 0, 0)
        self.patch_button = QPushButton("Patch…")
        self.patch_button.clicked.connect(self.open_patches)
        patch_box.addWidget(self.patch_button)
        self.patch_label = muted("")
        self.patch_label.setWordWrap(True)
        patch_box.addWidget(self.patch_label, 1)
        file_card.add(self.patch_row)
        self.patch_row.hide()

        # BCM only: the stock full-span image the module currently runs. The plan
        # diffs against it and writes just the 16 KiB sectors that differ.
        self.bcm_reference = None
        self.reference_card = self.add(Card("Stock reference image"))
        ref_row = QHBoxLayout()
        self.reference_button = QPushButton("Choose stock reference…")
        self.reference_button.clicked.connect(self._choose_reference)
        ref_row.addWidget(self.reference_button)
        self.reference_label = muted("No stock reference chosen")
        ref_row.addWidget(self.reference_label, 1)
        self.reference_card.add(ref_row)
        self.reference_card.add(muted(
            "The BCM writes only the 16 KiB sectors that differ from this stock image. "
            "Point it at the full-span .bin the BCM currently runs."))

        # BCM only: the separate RAM-driver .bin the loader stages as download #1
        # before the first application page. A verified driver is required; the
        # worker auto-discovers one beside the image, or it can be chosen here.
        self.bcm_driver = None
        self.driver_card = self.add(Card("RAM driver"))
        driver_row = QHBoxLayout()
        self.driver_button = QPushButton("Choose driver .bin…")
        self.driver_button.clicked.connect(self._choose_driver)
        driver_row.addWidget(self.driver_button)
        self.driver_label = muted("Looking for the driver beside the image…")
        driver_row.addWidget(self.driver_label, 1)
        self.driver_card.add(driver_row)
        self.driver_card.add(muted(
            "The BCM loader needs a separate 918-byte RAM driver (driver_ram_003800.bin) "
            "staged before any application page. Put it next to the image, or choose it here. "
            "Flashing is blocked until a verified driver is available."))

        self.range_card = self.add(Card("2. What to write"))
        self.reason = self.range_card.add(muted(""))
        self.plan_notes = self.range_card.add(Banner())
        self.options_box = QVBoxLayout()
        self.range_card.add(self.options_box)
        self.option_group = QButtonGroup(self)
        self.option_group.buttonToggled.connect(lambda *_: self.update_ready())
        self.option_group.buttonClicked.connect(lambda *_: self._schedule_commit())

        advanced = self.add(Card())
        self.advanced_toggle = QCheckBox("Show advanced options")
        self.advanced_toggle.toggled.connect(self._toggle_advanced)
        self.advanced_toggle.toggled.connect(lambda *_: self._schedule_commit())
        advanced.add(self.advanced_toggle)
        self.advanced = QWidget()
        adv = QVBoxLayout(self.advanced)
        adv.setContentsMargins(22, 0, 0, 0)
        self.correct = QCheckBox("Fix wrong internal checksums (only for images you edited yourself)")
        self.simulator = QCheckBox("Bench simulator patches. BENCH ONLY: never put this in a car")
        self.recovery = QCheckBox("Recovery: the module is stuck in its bootloader and won't identify")
        self.no_backup = QCheckBox("Flash without a backup (not recommended)")
        self.crossflash = QCheckBox("Crossflash: allow an image for a different part number")
        for box in (self.correct, self.simulator, self.recovery, self.no_backup, self.crossflash):
            adv.addWidget(box)
            box.toggled.connect(self._options_changed)
            box.toggled.connect(lambda *_: self._schedule_commit())
        advanced.add(self.advanced)
        self.advanced.hide()

        checklist = self.add(Card("3. Before you flash"))
        self.checks = [QCheckBox(text) for text in (
            "Battery charger or a stable 13-14 V supply is connected",
            "Ignition on, engine off",
            "This computer is plugged in and won't be closed or unplugged")]
        for box in self.checks:
            checklist.add(box)
            box.toggled.connect(lambda *_: self.update_ready())

        go = self.add(Card("4. Flash"))
        self.summary = go.add(muted(""))
        self.flash_button = go.add(HoldButton("Hold to flash", seconds=3))
        self.flash_button.confirmed.connect(self.flash)
        self.progress = go.add(ProgressPanel())
        self.result_banner = go.add(Banner())
        self.cycle_button = QPushButton("I've cycled the ignition: check again")
        self.cycle_button.clicked.connect(self.check_after_cycle)
        self.cycle_button.hide()
        go.add(self.cycle_button)
        self.expected_version = None
        self.finish()
        self.history.reset(self._snapshot())
        self._update_undo_buttons()
        state.progress.connect(self.on_progress)
        state.open_in_flash.connect(self.load)
        state.open_patched_in_flash.connect(lambda path, source: self.load(path, patched_from=source))
        state.start_recovery.connect(self.begin_recovery)

    def ai_context(self):
        """What the Assistant sees of this page (None: no image chosen)."""
        if not self.inspection:
            return None
        ident = self.state.identity or {}
        lines = [f"Module: {(self.state.family or {}).get('label', '?')}, part on the car "
                 f"{self.state.part() or 'unknown'}, software {ident.get('sw_version', '?')}",
                 f"Image: {self.inspection.get('name')} ({self.inspection.get('size')} bytes)"]
        for check in self.inspection.get("checks") or []:
            lines.append(f"- check {check.get('key')}: {check.get('status', '')} {check.get('detail', '')}".strip())
        if self.patch_info:
            lines.append(f"Patched from {Path(self.patch_source).name}: {self.patch_info.get('summary', '')}")
        if self.plan:
            lines.append(f"Plan: {self.plan.get('reason', '')}")
            for note in self.plan.get("notes") or []:
                lines.append(f"- note: {note}")
            for option in self.plan.get("options") or []:
                lines.append(f"- option {option.get('label')}: {option.get('size')} bytes, "
                             f"{'valid' if option.get('valid') else 'not possible: ' + str(option.get('reason'))}")
        chosen = self.selected_option()
        if chosen:
            lines.append(f"Chosen: {chosen.get('label')}")
        flags = [name for name in self.FLAGS if getattr(self, name).isChecked()]
        lines.append("Advanced options on: " + (", ".join(flags) if flags else "none"))
        lines.append("Has a backup of this module: " + ("yes" if self.state.record and self.state.record.has_backup() else "no"))
        return {"title": "flash review", "context": "\n".join(lines), "question":
               "Review this flash before I do it. Does the image match the module, is the write plan "
               "sensible, and is there anything risky I should fix first?",
               "extra": "You are giving a second opinion only. The app runs its own safety checks; "
                        "never tell the user to bypass them."}

    # --- undo / redo ---

    FLAGS = ("correct", "simulator", "recovery", "no_backup", "crossflash")

    def _snapshot(self):
        checked = self.option_group.checkedButton()
        return {
            "path": str(self.path) if self.path else None,
            "patch_source": str(self.patch_source) if self.patch_source else None,
            "patch_info": self.patch_info,
            "advanced": self.advanced_toggle.isChecked(),
            "flags": {name: getattr(self, name).isChecked() for name in self.FLAGS},
            "option": (checked.property("option") or {}).get("key") if checked else None,
        }

    def _schedule_commit(self):
        if not self._restoring:
            QTimer.singleShot(0, self._commit)

    def _commit(self):
        if self._restoring:
            return
        self.history.commit(self._snapshot())
        self._update_undo_buttons()

    def _update_undo_buttons(self):
        idle = not self.state.busy
        self.undo_button.setEnabled(idle and self.history.can_undo())
        self.redo_button.setEnabled(idle and self.history.can_redo())

    def undo(self):
        if not self.state.busy and self.history.can_undo():
            self._restore(self.history.undo())

    def redo(self):
        if not self.state.busy and self.history.can_redo():
            self._restore(self.history.redo())

    def _restore(self, snap):
        self._restoring = True
        try:
            self.advanced_toggle.blockSignals(True)
            self.advanced_toggle.setChecked(snap["advanced"])
            self.advanced.setVisible(snap["advanced"])
            self.advanced_toggle.blockSignals(False)
            for name in self.FLAGS:
                box = getattr(self, name)
                box.blockSignals(True)
                box.setChecked(snap["flags"][name])
                box.blockSignals(False)
            self._pending_option = snap["option"]
            same_file = snap["path"] == (str(self.path) if self.path else None) \
                and snap["patch_source"] == (str(self.patch_source) if self.patch_source else None)
            if snap["path"] is None:
                self._reset_file()
                self.refresh()
            elif same_file and self.plan:
                self._planned(self.plan)  # re-applies the option choice
            else:
                self.load(snap["path"], patched_from=snap["patch_source"], patch_info=snap["patch_info"])
        finally:
            self._restoring = False
        self._update_undo_buttons()

    # --- state ---

    def begin_recovery(self):
        """From a Recover button on the Connect page: preset recovery mode."""
        from . import security
        if not security.require(self, self.state.settings, "recovery"):
            return
        self._reset_file()
        self.advanced_toggle.setChecked(True)
        self.recovery.blockSignals(True)
        self.recovery.setChecked(True)
        self.recovery.blockSignals(False)
        self.result_banner.show_message(
            f"Recovery for the {self.state.family['label']}: choose the exact image made for this "
            "module. Recovery skips the checks that it's the module you think it is, so the "
            "wrong image bricks it.", "warning")
        self.refresh()

    def _toggle_advanced(self, on):
        self.advanced.setVisible(on)
        if not on:
            for box in (self.correct, self.simulator, self.recovery, self.no_backup,
                        self.crossflash):
                box.setChecked(False)
        if self.plan:
            self._planned(self.plan)

    def _options_changed(self, *_):
        if self.crossflash.isChecked() and self.sender() is self.crossflash:
            MessageBox.warning(self, "Crossflash",
                                "A crossflash puts firmware for a different part number on "
                                "this module, and it won't work as before without further "
                                "steps.\n\nOnly do this if you know exactly what your car needs.")
        if self.recovery.isChecked() and self.sender() is self.recovery:
            from . import security
            if not security.require(self, self.state.settings, "recovery"):
                self.recovery.blockSignals(True)
                self.recovery.setChecked(False)
                self.recovery.blockSignals(False)
                return
            MessageBox.warning(self, "Recovery mode",
                                "Recovery skips every check that the module is what you think it "
                                "is. Only use it when the module is stuck in its bootloader, and "
                                "only with the exact image made for it.\n\n"
                                "Flashing the wrong image in recovery mode bricks the module.")
        if self.path:
            self.load(self.path, patched_from=self.patch_source, patch_info=self.patch_info)
        else:
            self.refresh()

    def family_name(self):
        return self.state.family["name"] if self.state.family else None

    def _choose_reference(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Choose stock reference image", "", "Firmware images (*.bin)")
        if not path:
            return
        self.bcm_reference = path
        self.reference_label.setText(Path(path).name)
        if self.path:
            # Re-plan against the new reference.
            self.load(self.path)

    def _choose_driver(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Choose BCM RAM driver", "", "Driver images (*.bin)")
        if not path:
            return
        self.bcm_driver = path
        if self.path:
            # Re-inspect so the chosen driver is verified and the badge updates.
            self.load(self.path)
        else:
            self.driver_label.setText(Path(path).name)

    def _set_driver_status(self):
        """Reflect the inspection's driver check on the Flash page."""
        info = (self.inspection or {}).get("driver") or {}
        if info.get("found"):
            self.driver_label.setText(f"Verified: {Path(info['path']).name}")
        elif info.get("explicit"):
            self.driver_label.setText(info.get("error") or "Driver file failed verification.")
        elif self.inspection is not None:
            self.driver_label.setText("No verified driver found — choose the driver .bin.")
        else:
            self.driver_label.setText("Looking for the driver beside the image…")

    def refresh(self):
        family = self.state.family
        if self.path and self.loaded_for != (self.family_name(), self.state.part()):
            self._reset_file()
        if hasattr(self, "history"):
            self._update_undo_buttons()
        self.simulator.setVisible(self.family_name() == HALDEX)
        self.no_backup.setVisible(self.family_name() in BACKUP_FAMILIES)
        self.reference_card.setVisible(self.family_name() == BCM)
        self.driver_card.setVisible(self.family_name() == BCM)
        patchable = (self.family_name() == EPS and bool(self.inspection)
                     and self.inspection.get("guessed_family") == EPS)
        self.patch_row.setVisible(patchable)
        self.patch_button.setEnabled(patchable and not self.state.busy)
        can_precond = (self.family_name() == BCM and self.state.family
                       and self.state.family.get("can_check_preconditions"))
        self.precond.setVisible(bool(can_precond))
        self.precond_button.setEnabled(bool(can_precond) and not self.state.busy)
        problem = None
        if not family:
            problem = "Choose a module at the top."
        elif not family.get("can_flash"):
            problem = f"Flashing isn't supported for the {family['label']}."
        elif not self.state.identity and not self.recovery.isChecked():
            problem = "Read the module's info on the Connect page first (Read info)."
        elif (self.family_name() in BACKUP_FAMILIES and self.state.record
              and not self.state.record.has_backup()
              and not self.no_backup.isChecked() and not self.recovery.isChecked()):
            problem = "Make a backup on the Back up page first."
        self.gate.show_message(problem or "", "warning")
        self.update_ready()

    def selected_option(self):
        button = self.option_group.checkedButton()
        return button.property("option") if button else None

    def blocking_reason(self):
        if self.state.busy:
            return "Working…"
        if self.gate.active:
            return self.gate.text()
        if not self.inspection:
            return "Choose an image."
        allowed = {"checksums": self.correct.isChecked(), "crossflash": self.crossflash.isChecked()}
        errors = [c for c in self.inspection["checks"]
                  if c["level"] == "error" and not allowed.get(c["key"], False)]
        if errors:
            driver_error = next((c for c in errors if c["key"] == "driver"), None)
            if driver_error:
                return (driver_error.get("detail")
                        or "The BCM RAM driver is missing or failed verification. Choose a "
                           "verified driver .bin before flashing.")
            if any(c["key"] == "crossflash" for c in self.inspection["checks"]):
                return ("This image is for a different part number (a crossflash). Allow it under "
                        "advanced options if that's what you want.")
            return "This image can't be flashed. See the red badges."
        option = self.selected_option()
        if not option:
            return self.plan["reason"] if self.plan and not self.plan.get("recommended") else "Choose what to write."
        if not all(box.isChecked() for box in self.checks):
            return "Tick every item in the checklist."
        return None

    def update_ready(self):
        reason = self.blocking_reason()
        self.flash_button.setEnabled(reason is None)
        option = self.selected_option()
        if reason:
            self.summary.setText(reason)
        else:
            self.summary.setText(
                f"Writes {option['label']} (0x{option['start']:05X}-0x{option['end']:05X}, "
                f"{kib(option['size'])}) from {Path(self.path).name}.")

    # --- programming conditions (BCM, read-only) ---

    def check_preconditions(self):
        self.precond_button.setEnabled(False)
        self.precond_note.hide()
        self.precond_banner.show_message(
            "Checking programming conditions… nothing is written to the module.", "info")

        def done(result):
            self.precond_button.setEnabled(not self.state.busy)
            report = result.get("result") or {}
            if result.get("error") or not report:
                self.precond_banner.show_message(
                    "Couldn't read the programming conditions. "
                    + str(result.get("error") or ""), "error")
                self.precond_note.hide()
                return
            if report.get("all_ok"):
                self.precond_banner.show_message(
                    f"Programming conditions OK — supply {report.get('supply_volts')} V.", "ok")
                self.precond_note.hide()
            else:
                blocking = report.get("blocking") or ["a programming condition isn't met"]
                self.precond_banner.show_message("Not ready: " + "; ".join(blocking), "warning")
                note = report.get("scaling_note")
                self.precond_note.setText(note or "")
                self.precond_note.setVisible(bool(note))

        def failed(error):
            self.precond_button.setEnabled(not self.state.busy)
            self.precond_note.hide()
            self.precond_banner.show_message(self.error_text(error), "error")

        self.state.run("preconditions", {}, done, failed)

    # --- image ---

    def _reset_file(self):
        self.path = self.inspection = self.plan = None
        self.patch_source = self.patch_info = None
        self.loaded_for = None
        self.drop.set_file("Drop a .bin file here, or click to choose", "")
        self.badges.clear()
        self.file_banner.hide()
        self.patch_label.setText("")
        self._clear_options()

    def load(self, path, converted=False, patched_from=None, patch_info=None):
        """Check ``path`` and plan its flash. ``patched_from`` is the image it
        was patched from (the plan diffs against it to pick the blocks)."""
        if self.state.busy:
            return
        if str(path).lower().endswith(".sgo"):
            self.file_banner.show_message("")
            convert_sgo_then(self, path, self.family_name(), self.file_banner,
                             lambda out: self.load(out, converted=True))
            return
        self.converted = converted
        if not converted:
            self.file_banner.show_message("")
        self.patch_source = patched_from
        self.patch_info = patch_info
        if patched_from:
            text = f"Patched copy of {Path(patched_from).name}"
            if patch_info and patch_info.get("summary"):
                text += f": {patch_info['summary']}"
            self.patch_label.setText(text + f". Saved as {Path(path).name}.")
        else:
            self.patch_label.setText("Apply patches to this image before flashing it.")
        self.path = path
        self.loaded_for = (self.family_name(), self.state.part())
        self.inspection = self.plan = None
        self.result_banner.hide()
        self.cycle_button.hide()
        self.drop.set_file(Path(path).name, "Checking…")
        self.badges.clear()
        self._clear_options()
        family = self.family_name()
        if not family:
            self.file_banner.show_message("Choose a module at the top first.", "warning")
            return
        self._schedule_commit()
        self.state.offline("inspect_image", {"path": path, "family": family,
                                             "ecu_part": self.state.part() or None,
                                             "ecu_version": (self.state.identity or {}).get("sw_version"),
                                             "driver": self.bcm_driver if family == BCM else None},
                           self._inspected, lambda e: self._file_error(e))

    def _file_error(self, error):
        self.drop.set_file(Path(self.path).name if self.path else "", "")
        self.file_banner.show_message(self.error_text(error), "error")
        self.refresh()

    def _inspected(self, result):
        self.inspection = result
        self._set_driver_status()
        self.drop.set_file(result["name"], f"{kib(result['size'])} · SHA-256 {result['sha256']}")
        badges = [(c["label"], c["level"], c.get("detail")) for c in result["checks"]]
        self.badges.set_badges(badges)
        errors = [c for c in result["checks"] if c["level"] == "error"]
        warnings = [c for c in result["checks"] if c["level"] == "warning"]
        if errors:
            text = "\n".join(c["label"] + (": " + c["detail"] if c["detail"] else "") for c in errors)
            if result.get("correctable") and not self.correct.isChecked():
                text += ("\n\nIf you edited this image yourself, turn on \"Fix wrong internal "
                         "checksums\" under advanced options. Otherwise the file is damaged or "
                         "incomplete: get a fresh copy.")
            self.file_banner.show_message(text, "error")
            allowable = {"crossflash"} | ({"checksums"} if result.get("correctable") else set())
            if not all(c["key"] in allowable for c in errors):
                self.refresh()
                return
        elif warnings:
            self.file_banner.show_message("\n".join(c["detail"] for c in warnings), "warning")
        elif not getattr(self, "converted", False):
            self.file_banner.show_message("")
        reference = None
        if self.family_name() == BCM:
            reference = self.bcm_reference
        elif self.state.record and self.family_name() in BACKUP_FAMILIES:
            reference = self.state.record.state()
        self.state.offline("plan_flash", {
            "path": self.path, "family": self.family_name(),
            "reference": str(reference) if reference else None,
            "ecu_version": (self.state.identity or {}).get("sw_version"),
            "correct_checksum": self.correct.isChecked(),
            "simulator_mode": self.simulator.isChecked(),
            "patched_from": self.patch_source if self.family_name() == EPS else None,
            "recovery": self.recovery.isChecked(),
        }, self._planned, self._file_error)

    # --- patching (EPS) ---

    def open_patches(self):
        """Patch the image on this page. Patches always go onto the original
        file, so choosing again replaces the earlier choice instead of stacking."""
        if self.state.busy or not self.path:
            return
        from . import security
        if not security.require(self, self.state.settings, "patch"):
            return
        from .disclaimer import confirm_patching
        if not confirm_patching(self):
            return
        source = self.patch_source or self.path
        self.state.offline("eps_patch_options", {"path": source, "current": self.path},
                           lambda options: self._choose_patches(source, options),
                           lambda e: self.file_banner.show_message(self.error_text(e), "error"))

    def _choose_patches(self, source, options):
        from .patch_dialog import PatchDialog
        dialog = PatchDialog(options, self)
        if dialog.exec() != PatchDialog.DialogCode.Accepted:
            return
        selection = dialog.selection()
        if PatchDialog.empty(selection):
            if self.patch_source:
                self.file_banner.show_message(f"No patches chosen: back to {Path(source).name}.", "info")
                self.load(source)
            return
        summary = PatchDialog.describe(selection)
        stem = f"{Path(source).stem}_patched_{timestamp()}"
        out = patched_dir() / f"{stem}{Path(source).suffix}"
        count = 1
        while out.exists():  # never overwrite an earlier patched copy
            count += 1
            out = patched_dir() / f"{stem}_{count}{Path(source).suffix}"

        def done(result):
            result["summary"] = summary
            self.load(result["out"], patched_from=source, patch_info=result)

        self.state.offline("eps_apply_patches", {"path": source, "out": str(out), **selection}, done,
                           lambda e: self.file_banner.show_message(
                               "Not patched: " + self.error_text(e), "error"))

    def _clear_options(self):
        for button in self.option_group.buttons():
            self.option_group.removeButton(button)
            button.deleteLater()
        while self.options_box.count():
            item = self.options_box.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self.reason.setText("")
        self.plan_notes.show_message("")

    def _planned(self, plan):
        self.plan = plan
        self._clear_options()
        self.reason.setText(plan["reason"])
        self.plan_notes.show_message("\n\n".join(plan.get("notes") or []), "warning")
        hidden = 0
        for option in plan["options"]:
            if (plan["family"] == HALDEX and option["key"] != "full"
                    and not self.advanced_toggle.isChecked()):
                hidden += 1
                continue
            text = f"{option['label']}   ·   {kib(option['size'])}"
            if option["key"] == plan.get("recommended"):
                text += "   ·   recommended"
            button = QRadioButton(text)
            button.setObjectName("option")
            button.setProperty("option", option)
            button.setToolTip(option.get("hint", ""))
            if not option["valid"]:
                button.setEnabled(False)
                button.setToolTip(option.get("reason", ""))
            self.option_group.addButton(button)
            self.options_box.addWidget(button)
            hint = muted(option.get("hint", "") if option["valid"] else "Not possible: " + option.get("reason", ""))
            hint.setContentsMargins(28, 0, 0, 4)
            self.options_box.addWidget(hint)
            if option["key"] == plan.get("recommended"):
                button.setChecked(True)
        if hidden:
            self.options_box.addWidget(muted(f"{hidden} smaller range(s) under advanced options."))
        if self._pending_option:
            for button in self.option_group.buttons():
                if button.isEnabled() and (button.property("option") or {}).get("key") == self._pending_option:
                    button.setChecked(True)
            self._pending_option = None
        self.refresh()

    # --- flashing ---

    def flash(self):
        if self.blocking_reason():
            return
        from . import security
        if not security.require(self, self.state.settings, "flash"):
            return
        option = self.selected_option()
        crossing = next((c for c in self.inspection["checks"] if c["key"] == "crossflash"), None)
        if crossing:
            text, ok = InputDialog.getText(
                self, "Confirm crossflash",
                f"{crossing['label']}.\nType CROSSFLASH to write it anyway:")
            if not ok or text.strip() != "CROSSFLASH":
                return
        if self.recovery.isChecked():
            text, ok = InputDialog.getText(
                self, "Confirm recovery flash",
                f"Recovery mode can't check the module.\nType RECOVER to flash "
                f"{Path(self.path).name} to the {self.state.family['label']}:")
            if not ok or text.strip() != "RECOVER":
                return
        params = {"input": self.path, "start": option["start"], "end": option["end"],
                  "correct_checksum": self.correct.isChecked(),
                  "simulator_mode": self.simulator.isChecked(),
                  "recovery": self.recovery.isChecked()}
        if option.get("sectors"):
            # BCM flashes by 16 KiB sector; the worker builds --bcm-sector args.
            params["sectors"] = list(option["sectors"])
        if self.family_name() == BCM:
            # Thread the verified RAM driver through to --bcm-driver. The flash
            # is gated on a found driver (blocking_reason), so this is present.
            driver = (self.inspection.get("driver") or {}).get("path")
            if driver:
                params["driver"] = driver
        self.result_banner.show_message("Flashing. Don't touch the car, the cable or this computer.", "warning")
        self.cycle_button.hide()
        self.progress.start("Connecting")
        QTimer.singleShot(50, lambda: self.verticalScrollBar().setValue(self.verticalScrollBar().maximum()))
        self.flashed_plan = dict(self.plan, flashed=option["key"])
        self.state.run("flash", params, lambda r: self._flashed(r, params, option), self._flash_failed)

    def on_progress(self, message):
        if self.progress.isVisibleTo(self) and self.state.busy:
            self.progress.update_from(message)

    def _flash_failed(self, error):
        # The worker couldn't run the flash at all; still look at the module.
        self._reidentify("failed", "The flash didn't run: " + self.error_text(error))

    def _flashed(self, result, params, option):
        report = result.get("result") or {}
        record = self.state.record
        entry = {"kind": "flash", "image": self.path, "sha256": self.inspection["sha256"],
                 "start": option["start"], "end": option["end"], "status": report.get("status"),
                 "boot_verified": report.get("boot_verified"), "recovery": params["recovery"],
                 "error": result.get("error")}
        if self.patch_source:
            entry["patched_from"] = self.patch_source
            entry["patches"] = {k: (self.patch_info or {}).get(k)
                                for k in ("features", "gains", "torque_trip", "summary")}
        if record and report.get("checksum_verified"):
            try:
                entry["kept_copy"] = record.keep_flashed(self.path)
            except OSError:
                pass
        if record:
            record.add(entry)
        if report.get("boot_verified") and report.get("commit_outcome") == "ambiguous":
            # Written, checksum-verified and seen running, but the ECU didn't
            # acknowledge every commit service (often it restarts before
            # answering StopCommunication). Not a bootloader case.
            if record:
                record.forget_state()
            unanswered = [f"0x{k.upper()}" for k, v in (report.get("commit_responses") or {}).items()
                          if v != "acknowledged"]
            self._reidentify("ambiguous", "Flashed and checksum-verified. The module didn't "
                             f"acknowledge the last commit step ({', '.join(unanswered) or 'commit'}), "
                             "which the ignition cycle below settles.")
            return
        if result.get("error") or report.get("status") != "ok":
            if record:
                record.forget_state()
            self.failed_needs_recovery = report.get("recovery_required", True)
            self._reidentify("failed", "Flash failed: " + str(
                result.get("error") or report.get("error") or report.get("status")))
            return
        if record and self.family_name() in BACKUP_FAMILIES:
            reference = record.state()
            self.state.offline("compose_state", {
                **{k: params[k] for k in ("correct_checksum", "simulator_mode")},
                "path": self.path, "family": self.family_name(), "start": option["start"],
                "end": option["end"], "reference": str(reference) if reference else None,
                "out": str(record.state_path) + ".new"},
                lambda r: self._state_written(r, record), lambda e: record.forget_state())
        self._reidentify("ok", "Flashed and verified.")

    def _state_written(self, result, record):
        new = Path(str(record.state_path) + ".new")
        if result.get("written") and new.is_file():
            new.replace(record.state_path)
        else:
            record.forget_state()

    # --- after a flash: identify, then confirm across an ignition cycle ---

    def _reidentify(self, outcome, headline):
        """The worker has released the adapter; ask the module what it is now.
        This also updates the Connect page and the status pill."""
        for box in self.checks:  # another flash needs a fresh confirmation
            box.setChecked(False)
        self.progress.start("Checking the module")
        self.state.run("identify", {},
                       lambda r: self._after_identify(outcome, headline, r),
                       lambda e: self._after_identify(outcome, headline,
                                                      {"error": self.error_text(e)}))

    @staticmethod
    def _identity_from(result):
        identity = result.get("result") or {}
        if result.get("error") or result.get("exit_code") or not identity:
            return None
        return identity

    def _after_identify(self, outcome, headline, result):
        self.progress.stop()
        identity = self._identity_from(result)
        self.state.set_identity(identity)
        running = identity and not identity.get("in_bootloader")
        if outcome in ("ok", "ambiguous") and running:
            self.expected_version = identity.get("sw_version", "")
            self.result_banner.show_message(
                f"{headline} It's running {self.state.part()} {self.expected_version} "
                f"({identity.get('system_desc', '')}).\n\n"
                "Last step: turn the ignition off, wait about 10 seconds, turn it back on (or "
                "start the engine), then press Check again.",
                "ok" if outcome == "ok" else "warning")
            self.cycle_button.show()
            self._dataset_prompt()
        elif outcome in ("ok", "ambiguous"):
            self.expected_version = None
            self.result_banner.show_message(
                f"{headline} But the module didn't identify straight afterwards.\n\n"
                "Turn the ignition off, wait about 10 seconds, turn it back on, then press "
                "Check again.", "warning")
            self.cycle_button.show()
        elif running:
            self.result_banner.show_message(
                f"{headline}\n\nThe module still runs its application "
                f"({self.state.part()} {identity.get('sw_version', '')}), so nothing was lost.",
                "error")
        else:
            advice = ("\n\nThe module is probably waiting in its bootloader. That's recoverable: "
                      "A scan on the Connect page shows it with a Recover button, or turn "
                      "on Recovery under advanced options and flash the same image again."
                      if getattr(self, "failed_needs_recovery", True) else "\n\nNothing was erased.")
            self.result_banner.show_message(headline + advice, "error")

    def check_after_cycle(self):
        self.cycle_button.setEnabled(False)
        self.progress.start("Checking after the ignition cycle")
        self.state.run("identify", {}, self._after_cycle,
                       lambda e: self._after_cycle({"error": self.error_text(e)}))

    def _after_cycle(self, result):
        self.progress.stop()
        self.cycle_button.setEnabled(True)
        identity = self._identity_from(result)
        if identity is None:
            self.result_banner.show_message(
                "The module didn't answer after the ignition cycle. Check the ignition is on, "
                "then press Check again. If it still doesn't answer, a scan on the Connect "
                "page shows whether it's waiting in its bootloader.", "warning")
            return
        self.state.set_identity(identity)
        version = identity.get("sw_version", "")
        if identity.get("in_bootloader"):
            self.result_banner.show_message(
                "After the ignition cycle the module came up in its bootloader. Flash it again "
                "with Recovery (Connect → Scan → Recover).", "error")
        elif self.expected_version and version != self.expected_version:
            self.result_banner.show_message(
                f"After the ignition cycle it reports {version}, not {self.expected_version}. "
                "Check what's on it before driving.", "warning")
        else:
            self.cycle_button.hide()
            if self.state.record:
                self.state.record.add({"kind": "verify", "sw_version": version,
                                       "identity": identity})
            self.result_banner.show_message(
                f"Confirmed after an ignition cycle: {self.state.part()} {version} "
                f"({identity.get('system_desc', '')}). You're done: go ahead and drive. Flashing "
                "doesn't set fault codes, and any faults left from the module being offline "
                "clear themselves once it's running normally.", "ok")

    def _dataset_prompt(self):
        plan = getattr(self, "flashed_plan", None) or {}
        if not (plan.get("needs_dataset") and plan.get("flashed") == "full"):
            return
        self.result_banner.show_message(
            self.result_banner.text() + "\n\nThis firmware came with the placeholder 000 "
            "dataset. Flash a real steer dataset next.", "warning")
        answer = MessageBox.question(
            self, "Flash a steer dataset",
            "The firmware you flashed carries the factory placeholder dataset (000), so the "
            "rack has no real steer dataset yet.\n\nChoose a dataset to flash now? Use a "
            "4 KiB dataset file or SGO, or a full image (its 0x5E block will be used).")
        if answer == MessageBox.StandardButton.Yes:
            path, _ = QFileDialog.getOpenFileName(self, "Choose steer dataset",
                                                  self.drop.start_dir,
                                                  "Firmware (*.bin *.sgo);;All files (*)")
            if path:
                self.load(path)
