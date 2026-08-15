from __future__ import annotations

from typing import Literal

from PySide6.QtWidgets import QHBoxLayout, QLabel, QStyle, QWidget


StatusTone = Literal["neutral", "busy", "success", "warning", "failure"]


class StatusLabel(QWidget):
    """Text-and-icon status; colour is never the only signal."""

    def __init__(self, name: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("statusPill")
        self.setAccessibleName(name)
        self.icon_label = QLabel()
        self.icon_label.setAccessibleName(f"{name} icon")
        self.text_label = QLabel()
        layout = QHBoxLayout(self)
        layout.setContentsMargins(7, 4, 7, 4)
        layout.setSpacing(6)
        layout.addWidget(self.icon_label)
        layout.addWidget(self.text_label, 1)
        self.set_status(
            "Not checked", QStyle.StandardPixmap.SP_MessageBoxInformation, tone="neutral"
        )

    def set_status(
        self,
        text: str,
        icon: QStyle.StandardPixmap,
        *,
        tone: StatusTone | None = None,
    ) -> None:
        """Set icon, text, and an optional presentation-only semantic tone."""

        resolved_tone = tone or self._tone_for(icon)
        self.setProperty("statusTone", resolved_tone)
        self.style().unpolish(self)
        self.style().polish(self)
        self.text_label.setText(text)
        self.icon_label.setPixmap(self.style().standardIcon(icon).pixmap(16, 16))
        self.setToolTip(text)
        self.setAccessibleDescription(
            f"Status: {text}. Semantic tone: {resolved_tone}."
        )

    @staticmethod
    def _tone_for(icon: QStyle.StandardPixmap) -> StatusTone:
        if icon == QStyle.StandardPixmap.SP_DialogApplyButton:
            return "success"
        if icon == QStyle.StandardPixmap.SP_MessageBoxCritical:
            return "failure"
        if icon == QStyle.StandardPixmap.SP_MessageBoxWarning:
            return "warning"
        if icon in {
            QStyle.StandardPixmap.SP_BrowserReload,
            QStyle.StandardPixmap.SP_MediaPlay,
        }:
            return "busy"
        return "neutral"

    def text(self) -> str:
        return self.text_label.text()

    def pixmap(self):
        return self.icon_label.pixmap()
