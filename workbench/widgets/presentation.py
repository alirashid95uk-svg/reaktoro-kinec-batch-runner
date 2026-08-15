"""Small, native-Qt presentation helpers used across workspaces."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)


def section_card(title: str, description: str = "") -> tuple[QFrame, QVBoxLayout]:
    card = QFrame()
    card.setObjectName("card")
    card.setAccessibleName(title)
    layout = QVBoxLayout(card)
    layout.setContentsMargins(16, 12, 16, 12)
    layout.setSpacing(8)
    heading = QLabel(title)
    heading.setObjectName("cardTitle")
    layout.addWidget(heading)
    if description:
        help_text = QLabel(description)
        help_text.setObjectName("mutedText")
        help_text.setWordWrap(True)
        layout.addWidget(help_text)
    return card, layout


def action_bar(*widgets: QWidget) -> QWidget:
    bar = QWidget()
    bar.setObjectName("actionBar")
    bar.setAccessibleName("Page actions")
    layout = QHBoxLayout(bar)
    layout.setContentsMargins(12, 8, 12, 8)
    layout.setSpacing(8)
    for widget in widgets:
        layout.addWidget(widget)
    layout.addStretch(1)
    return bar


class Disclosure(QWidget):
    """A keyboard-operable expandable region for secondary information."""

    def __init__(self, title: str, content: QWidget, *, expanded: bool = False) -> None:
        super().__init__()
        self.setAccessibleName(title)
        self.toggle = QToolButton()
        self.toggle.setText(title)
        self.toggle.setCheckable(True)
        self.toggle.setChecked(expanded)
        self.toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.toggle.setArrowType(Qt.ArrowType.DownArrow if expanded else Qt.ArrowType.RightArrow)
        self.toggle.setAccessibleName(title)
        self.content = content
        self.content.setVisible(expanded)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        layout.addWidget(self.toggle)
        layout.addWidget(self.content)
        self.toggle.toggled.connect(self.set_expanded)

    def move_toggle_to(self, target_layout) -> None:
        """Place the disclosure control in an existing compact action row."""

        self.layout().removeWidget(self.toggle)
        target_layout.addWidget(self.toggle)

    def set_expanded(self, expanded: bool) -> None:
        self.toggle.setArrowType(
            Qt.ArrowType.DownArrow if expanded else Qt.ArrowType.RightArrow
        )
        self.content.setVisible(expanded)


class EmptyState(QFrame):
    def __init__(self, title: str, detail: str) -> None:
        super().__init__()
        self.setObjectName("emptyState")
        self.setAccessibleName(title)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.addStretch(1)
        heading = QLabel(title)
        heading.setObjectName("cardTitle")
        heading.setAlignment(Qt.AlignmentFlag.AlignCenter)
        body = QLabel(detail)
        body.setObjectName("mutedText")
        body.setWordWrap(True)
        body.setAlignment(Qt.AlignmentFlag.AlignCenter)
        body.setMaximumWidth(520)
        body.setMinimumHeight(body.fontMetrics().lineSpacing() * 5)
        body.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)
        self.body = body
        layout.addWidget(heading, 0, Qt.AlignmentFlag.AlignHCenter)
        layout.addWidget(body, 0, Qt.AlignmentFlag.AlignHCenter)
        layout.addStretch(1)
