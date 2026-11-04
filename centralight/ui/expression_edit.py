"""The "=" button next to a setting, and the expression dialog it opens.

An expression is a small formula that sets the value, like a spreadsheet cell: for example
"sin(time) * 0.5 + 0.5" or "mix(#FF0000, accent, audio.bass)". The dialog completes names as
you type (variables, functions, audio.*), shows the value it gives right now, and says in red
what's wrong if anything is. The setting's own value stays, as `value`, and is used whenever
the expression can't be worked out.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QCompleter,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ..expressions import AUDIO_NAMES, FUNCTIONS, SPECIALS, check
from .widgets import plain_tip

WORD_BEFORE_CURSOR = re.compile(r"[A-Za-z_][A-Za-z0-9_.]*$")
EXAMPLES = (
    ("A slow pulse", "wave(time, 0.25)"),
    ("Follow the bass", "audio.bass"),
    ("Twice as bright as set", "value * 2"),
    ("Round the rainbow", "hsv(time / 10)"),
)

# (value text, problem text): what the expression gives now, or what's wrong ("" = fine)
Evaluator = Callable[[str], tuple[str, str]]


class ExprButton(QToolButton):
    """The "=" next to a setting: blue when the setting has an expression, red when that
    expression has a problem."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("ExprButton")
        self.setText("=")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAccessibleName("Expression")
        self.set_state("", "")

    def set_state(self, text: str, problem: str) -> None:
        active, broken = bool(text), bool(text and problem)
        if self.property("active") != active or self.property("error") != broken:
            self.setProperty("active", active)
            self.setProperty("error", broken)
            self.style().unpolish(self)
            self.style().polish(self)
        if not text:
            self.setToolTip("Set this with an expression (a formula, e.g. sin(time))")
        elif broken:  # (a formula keeps its "<" and ">": escaped, so it's never read as HTML)
            self.setToolTip(plain_tip(f"= {text}\nProblem: {problem}\n(the setting's own value is used meanwhile)"))
        else:
            self.setToolTip(plain_tip(f"= {text}"))


def completion_words(variables: Iterable[str]) -> list[str]:
    words = set(variables) | set(SPECIALS) | {"pi"} | {f"audio.{n}" for n in AUDIO_NAMES}
    words |= {f"{name}(" for name in FUNCTIONS}
    return sorted(words, key=str.casefold)


class ExpressionDialog(QDialog):
    """Write or change one setting's expression."""

    applied = Signal(str)  # the new expression ("" = remove it)

    def __init__(self, title: str, text: str, variables: Iterable[str], evaluate: Evaluator, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"Expression: {title}")
        self.setModal(True)
        self._evaluate = evaluate
        self._had_expression = bool(text)

        heading = QLabel(f"{title} =")
        heading.setObjectName("RowName")
        self.edit = QLineEdit(text)
        self.edit.setObjectName("ExpressionEdit")
        self.edit.setPlaceholderText("e.g. wave(time, 0.25)  or  mix(#FF0000, accent, audio.bass)")
        self.edit.setClearButtonEnabled(True)
        self.completer = QCompleter(completion_words(variables), self)
        self.completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self.completer.setWidget(self.edit)
        self.completer.setCompletionMode(QCompleter.CompletionMode.PopupCompletion)
        self.completer.activated.connect(self._insert)
        self.edit.textEdited.connect(self._complete)
        self.edit.textChanged.connect(self._refresh)
        self.edit.returnPressed.connect(self._apply)

        self.value_label = QLabel("")
        self.value_label.setObjectName("ExpressionValue")
        self.problem_label = QLabel("")
        self.problem_label.setObjectName("ExpressionProblem")
        self.problem_label.setWordWrap(True)

        names = sorted(n for n in variables)
        help_text = (
            "<b>value</b> is this setting's own value, <b>time</b> the seconds since the input started, "
            "<b>audio.bass</b> (and level, mid, treble, beat, pitch) the music, 0–1."
            + (f" Variables: {', '.join(names)}." if names else "")
            + " Functions: " + ", ".join(sorted(FUNCTIONS)) + "."
        )
        help_label = QLabel(help_text)
        help_label.setObjectName("Hint")
        help_label.setWordWrap(True)
        help_label.setTextFormat(Qt.TextFormat.RichText)

        examples = QHBoxLayout()
        examples.setSpacing(6)
        for label, example in EXAMPLES:
            button = QPushButton(label)
            button.setToolTip(example)
            button.clicked.connect(lambda _c=False, e=example: self._use_example(e))
            examples.addWidget(button)
        examples.addStretch(1)

        self.buttons = QDialogButtonBox()
        self.use_button = self.buttons.addButton("Use it", QDialogButtonBox.ButtonRole.AcceptRole)
        self.use_button.setProperty("accent", True)
        self.remove_button = self.buttons.addButton("Remove the expression", QDialogButtonBox.ButtonRole.DestructiveRole)
        self.remove_button.setVisible(self._had_expression)
        self.buttons.addButton(QDialogButtonBox.StandardButton.Cancel)
        self.use_button.clicked.connect(self._apply)
        self.remove_button.clicked.connect(self._remove)
        self.buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.setSpacing(10)
        layout.addWidget(heading)
        layout.addWidget(self.edit)
        layout.addWidget(self.value_label)
        layout.addWidget(self.problem_label)
        layout.addLayout(examples)
        layout.addWidget(help_label)
        layout.addWidget(self.buttons)
        self.setMinimumWidth(min(620, max(480, self.fontMetrics().horizontalAdvance("m") * 48)))

        self._timer = QTimer(self)  # the value moves with time: keep it fresh
        self._timer.setInterval(250)
        self._timer.timeout.connect(self._refresh)
        self._timer.start()
        self._refresh()
        self.edit.setFocus()

    # --- completing names

    def _word(self) -> str:
        match = WORD_BEFORE_CURSOR.search(self.edit.text()[: self.edit.cursorPosition()])
        return match.group(0) if match else ""

    def _complete(self, _text: str = "") -> None:
        word = self._word()
        popup = self.completer.popup()
        if not word:
            popup.hide()
            return
        self.completer.setCompletionPrefix(word)
        count = self.completer.completionCount()
        if count == 0 or (count == 1 and self.completer.currentCompletion().rstrip("(") == word):
            popup.hide()
            return
        rect = self.edit.cursorRect()
        rect.setWidth(max(200, popup.sizeHintForColumn(0) + 24))
        self.completer.complete(rect)

    def _insert(self, completion: str) -> None:
        word = self._word()
        position = self.edit.cursorPosition()
        text = self.edit.text()
        start = position - len(word)
        self.edit.setText(text[:start] + completion + text[position:])
        self.edit.setCursorPosition(start + len(completion))

    def _use_example(self, example: str) -> None:
        self.edit.setText(example)
        self.edit.setFocus()

    # --- checking and saving

    def text(self) -> str:
        return self.edit.text().strip()

    def _refresh(self) -> None:
        text = self.text()
        if not text:
            self.value_label.setText("No expression: the setting keeps its own value.")
            self.problem_label.setText("")
            self.use_button.setEnabled(self._had_expression)
            return
        problem = check(text)
        if problem:  # it can't even be read: can't be used
            self.value_label.setText("")
            self.problem_label.setText(problem)
            self.use_button.setEnabled(False)
            return
        value, problem = self._evaluate(text)
        self.value_label.setText(f"Right now: {value}" if value else "")
        self.problem_label.setText(f"{problem} (the setting's own value is used until that's fixed)" if problem else "")
        self.use_button.setEnabled(True)

    def _apply(self) -> None:
        text = self.text()
        if text and check(text):
            return
        self.applied.emit(text)
        self.accept()

    def _remove(self) -> None:
        self.applied.emit("")
        self.accept()

    def done(self, result: int) -> None:  # noqa: D401 (Qt name)
        self._timer.stop()
        super().done(result)
