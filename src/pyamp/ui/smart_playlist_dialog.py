# SPDX-License-Identifier: GPL-3.0-or-later
#
# PyAmp - a lightweight, Winamp-inspired music player
# Copyright (C) 2026 Kamran Zafar
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.
#
# Additional terms apply under section 7 of the GNU GPL; see ADDITIONAL-TERMS.md.

"""The dialog that makes or changes a smart playlist.

Each rule is a row: a field, how to compare it, and a value editor that suits the
field. While the rules are edited, the dialog counts the tracks they match, on a worker
thread so a big library never makes typing stutter.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

from PySide6.QtCore import QTimer, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from pyamp.library.saved_playlists import PlaylistNameError
from pyamp.library.smart_playlists import (
    MAX_DAYS,
    MAX_LIMIT,
    MAX_NUMBER,
    OPERATORS,
    FieldKind,
    Operator,
    Rule,
    RuleField,
    SmartPlaylist,
    SortOrder,
    problem,
)
from pyamp.ui.background_calls import BackgroundCalls
from pyamp.ui.dialog_parts import (
    ComboBox,
    heading,
    hint,
    inset,
    link_button,
    remove_button,
    separator,
    set_status,
    status_label,
)

PREVIEW_DELAY_MS = 300
FILE_TYPES = ("flac", "mp3", "m4a", "ogg", "opus", "wav", "aiff", "wma")
#: What a number or days box starts at when its field is picked.
_DEFAULT_NUMBERS = {RuleField.YEAR: 2000, RuleField.LENGTH: 240, RuleField.ADDED: 30}
#: The unit shown after a number.
_UNITS = {RuleField.LENGTH: " seconds", RuleField.ADDED: " days"}


class RuleRow(QWidget):
    """One rule: field, operator and value, and a button that removes the row."""

    changed = Signal()
    remove_requested = Signal(object)  # this row

    def __init__(self, rule: Rule, folders: Sequence[str], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.field = ComboBox()
        for rule_field in RuleField:
            self.field.addItem(rule_field.label, rule_field)
        self.operator = ComboBox()
        self.text = QLineEdit()
        self.text.setPlaceholderText("text to match")
        self.choice = ComboBox()  # file types or folders, or typed
        self.choice.setEditable(True)
        self._folders = list(folders)
        self.number = QSpinBox()
        self.number.setRange(0, MAX_NUMBER)
        self.and_label = QLabel("and")
        self.to = QSpinBox()
        self.to.setRange(0, MAX_NUMBER)
        self.remove = remove_button("Remove this rule")

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        widgets: tuple[QWidget, ...] = (self.field, self.operator, self.text, self.choice)
        for widget in (*widgets, self.number, self.and_label, self.to):
            layout.addWidget(widget)
        layout.addStretch()  # takes the room the text boxes leave when hidden
        layout.addWidget(self.remove)
        layout.setStretch(2, 1)
        layout.setStretch(3, 1)

        self.set_rule(rule)
        self.field.currentIndexChanged.connect(self._on_field)
        self.operator.currentIndexChanged.connect(self._on_operator)
        self.text.textChanged.connect(self.changed)
        self.choice.currentTextChanged.connect(self.changed)
        self.number.valueChanged.connect(self.changed)
        self.to.valueChanged.connect(self.changed)
        self.remove.clicked.connect(lambda: self.remove_requested.emit(self))

    def set_rule(self, rule: Rule) -> None:
        self.field.blockSignals(True)
        self.field.setCurrentIndex(self.field.findData(rule.field))
        self.field.blockSignals(False)
        self._fill(rule.field, rule.operator)
        if isinstance(rule.value, str):
            self.text.setText(rule.value)
            self.choice.setCurrentText(rule.value)
        else:
            self.number.setValue(rule.value)
        self.to.setValue(rule.to)
        self._show_editors()

    def rule(self) -> Rule:
        rule_field, operator = self.current_field(), self.current_operator()
        match rule_field.kind:
            case FieldKind.TEXT:
                return Rule(rule_field, operator, self.text.text())
            case FieldKind.FILE_TYPE | FieldKind.FOLDER:
                return Rule(rule_field, operator, self.choice.currentText())
            case FieldKind.NUMBER if operator is Operator.BETWEEN:
                return Rule(rule_field, operator, self.number.value(), self.to.value())
            case _:
                return Rule(rule_field, operator, self.number.value())

    # Qt hands item data back as plain strings, so they are made enums again.
    def current_field(self) -> RuleField:
        return RuleField(self.field.currentData())

    def current_operator(self) -> Operator:
        return Operator(self.operator.currentData())

    def _fill(self, rule_field: RuleField, operator: Operator | None = None) -> None:
        """Offer the operators and choices ``rule_field`` has, picking ``operator``."""
        kind = rule_field.kind
        self.operator.blockSignals(True)
        self.operator.clear()
        for option in OPERATORS[kind]:
            self.operator.addItem(option.label, option)
        self.operator.setCurrentIndex(max(0, self.operator.findData(operator)))
        self.operator.blockSignals(False)
        self.choice.blockSignals(True)
        self.choice.clear()
        self.choice.addItems(FILE_TYPES if kind is FieldKind.FILE_TYPE else self._folders)
        self.choice.blockSignals(False)
        if kind is FieldKind.DATE:
            self.number.setRange(1, MAX_DAYS)
        else:
            self.number.setRange(0, MAX_NUMBER)
        for box in (self.number, self.to):
            box.setSuffix(_UNITS.get(rule_field, ""))
            box.setGroupSeparatorShown(False)

    def _on_field(self) -> None:
        rule_field = self.current_field()
        self._fill(rule_field)
        if rule_field in _DEFAULT_NUMBERS:
            self.number.setValue(_DEFAULT_NUMBERS[rule_field])
            self.to.setValue(_DEFAULT_NUMBERS[rule_field])
        self._show_editors()
        self.changed.emit()

    def _on_operator(self) -> None:
        if self.current_operator() is Operator.BETWEEN:
            self.to.setValue(max(self.to.value(), self.number.value()))
        self._show_editors()
        self.changed.emit()

    def _show_editors(self) -> None:
        kind = self.current_field().kind
        between = self.current_operator() is Operator.BETWEEN
        self.text.setVisible(kind is FieldKind.TEXT)
        self.choice.setVisible(kind in (FieldKind.FILE_TYPE, FieldKind.FOLDER))
        self.number.setVisible(kind in (FieldKind.NUMBER, FieldKind.DATE))
        self.and_label.setVisible(between)
        self.to.setVisible(between)


class SmartPlaylistDialog(QDialog):
    """Makes a smart playlist, or changes ``playlist``; ``save`` is called on OK, and a
    :class:`PlaylistNameError` it raises (e.g. the name is taken) keeps the dialog open."""

    def __init__(
        self,
        playlist: SmartPlaylist | None,
        *,
        save: Callable[[SmartPlaylist], object],
        count: Callable[[SmartPlaylist], int],
        folders: Sequence[str] = (),
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Edit Smart Playlist" if playlist else "New Smart Playlist")
        self.setMinimumWidth(640)
        self._save = save
        self._count = count
        self._folders = list(folders)
        self._calls = BackgroundCalls(self, threads=1)
        self._request = 0
        self._preview = QTimer(self, singleShot=True, interval=PREVIEW_DELAY_MS)
        self._preview.timeout.connect(self._count_matches)
        playlist = playlist or SmartPlaylist("", (Rule(RuleField.GENRE, Operator.CONTAINS),))

        self.name = QLineEdit(playlist.name)
        name_font = self.name.font()
        name_font.setPointSizeF(name_font.pointSizeF() * 1.25)
        self.name.setFont(name_font)
        self.name.setMinimumHeight(32)
        self.name.setPlaceholderText("Name, e.g. 90s Rock")
        self.match = ComboBox()
        self.match.addItem("all", True)
        self.match.addItem("any", False)
        self.match.setCurrentIndex(0 if playlist.match_all else 1)
        self.rows: list[RuleRow] = []
        self._rules = QVBoxLayout()
        self._rules.setSpacing(6)
        self.no_rules = hint("No rules: every track in the library matches.")
        self.add_rule_button = link_button("+  &Add Rule")
        self.sort = ComboBox()
        for order in SortOrder:
            self.sort.addItem(order.label, order)
        self.sort.setCurrentIndex(self.sort.findData(playlist.sort))
        self.limited = QCheckBox("Limit to")
        self.limited.setChecked(playlist.limit > 0)
        self.limit = _limit_box(playlist.limit)
        self.status = status_label()
        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self.ok_button = self.buttons.button(QDialogButtonBox.StandardButton.Ok)
        self.ok_button.setText("Save" if playlist.name else "Create")

        self._layout()
        for rule in playlist.rules:
            self.add_rule(rule)
        self._connect()
        self.name.setFocus()
        self._changed()

    def _connect(self) -> None:
        self.name.textChanged.connect(self._changed)
        self.match.currentIndexChanged.connect(self._changed)
        self.sort.currentIndexChanged.connect(self._changed)
        self.limited.toggled.connect(self.limit.setEnabled)
        self.limited.toggled.connect(self._changed)
        self.limit.valueChanged.connect(self._changed)
        self.add_rule_button.clicked.connect(self._add_empty_rule)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)

    def _layout(self) -> None:
        rules_header = QHBoxLayout()
        rules_header.addWidget(heading("Rules"))
        rules_header.addStretch()
        rules_header.addWidget(QLabel("Match"))
        rules_header.addWidget(self.match)
        rules_header.addWidget(QLabel("of them"))

        panel = inset()
        inside = QVBoxLayout(panel)
        inside.setContentsMargins(10, 10, 10, 10)
        inside.setSpacing(8)
        inside.addLayout(self._rules)
        inside.addWidget(self.no_rules)
        add_line = QHBoxLayout()
        add_line.addWidget(self.add_rule_button)
        add_line.addStretch()
        inside.addLayout(add_line)

        order_line = QHBoxLayout()
        order_line.addWidget(QLabel("Sort by"))
        order_line.addWidget(self.sort, 1)
        order_line.addSpacing(24)
        order_line.addWidget(self.limited)
        order_line.addWidget(self.limit)

        footer = QHBoxLayout()
        footer.addWidget(self.status, 1)
        footer.addWidget(self.buttons)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 14)
        layout.setSpacing(8)
        layout.addWidget(heading("Smart Playlist"))
        layout.addWidget(self.name)
        layout.addSpacing(8)
        layout.addLayout(rules_header)
        layout.addWidget(panel)
        layout.addSpacing(8)
        layout.addWidget(heading("Order"))
        layout.addLayout(order_line)
        layout.addSpacing(6)
        layout.addWidget(separator())
        layout.addLayout(footer)

    def playlist(self) -> SmartPlaylist:
        """The smart playlist as the dialog shows it."""
        return SmartPlaylist(
            self.name.text(),
            tuple(row.rule() for row in self.rows),
            match_all=bool(self.match.currentData()),
            sort=SortOrder(self.sort.currentData()),
            limit=self.limit.value() if self.limited.isChecked() else 0,
        )

    def add_rule(self, rule: Rule | None = None) -> RuleRow:
        row = RuleRow(rule or Rule(RuleField.ARTIST, Operator.CONTAINS), self._folders, self)
        row.changed.connect(self._changed)
        row.remove_requested.connect(self.remove_rule)
        self.rows.append(row)
        self._rules.addWidget(row)
        self.no_rules.hide()
        self._changed()
        return row

    def _add_empty_rule(self) -> None:
        self.add_rule()

    def remove_rule(self, row: RuleRow) -> None:
        self.rows.remove(row)
        self._rules.removeWidget(row)
        row.deleteLater()
        self.no_rules.setVisible(not self.rows)
        self._changed()

    def accept(self) -> None:
        playlist = self.playlist()
        if message := problem(playlist):
            set_status(self.status, message, "problem")
            return
        try:
            self._save(playlist)
        except PlaylistNameError as exc:
            set_status(self.status, str(exc), "problem")
            return
        super().accept()

    def done(self, result: int) -> None:
        self._preview.stop()
        self._request += 1  # a count still running is no longer wanted
        self._calls.wait()
        super().done(result)

    def _changed(self) -> None:
        """Check the playlist now, and count its tracks once the user pauses."""
        self._request += 1
        message = problem(self.playlist())
        self.ok_button.setEnabled(not message)
        if message:
            self._preview.stop()
            set_status(self.status, message, "problem")
        else:
            set_status(self.status, "Counting…", "busy")
            self._preview.start()

    def _count_matches(self) -> None:
        self._request += 1
        request, playlist = self._request, self.playlist()

        def done(count: int | None, error: BaseException | None) -> None:
            if request != self._request:
                return  # the rules changed since
            if error is not None or count is None:
                set_status(self.status, f"Couldn't count the tracks: {error}", "problem")
            else:
                every = "" if playlist.rules else " (no rules: every track)"
                noun = "track matches" if count == 1 else "tracks match"
                set_status(self.status, f"{count:,} {noun}{every}", "count")

        self._calls.run(lambda: self._count(playlist), done)


def _limit_box(limit: int) -> QSpinBox:
    """How many tracks at most: enabled only while there's a limit (default 50)."""
    box = QSpinBox()
    box.setRange(1, MAX_LIMIT)
    box.setValue(limit or 50)
    box.setSuffix(" tracks")
    box.setFixedWidth(110)
    box.setEnabled(limit > 0)
    return box
