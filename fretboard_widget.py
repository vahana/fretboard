from PyQt6.QtWidgets import QWidget
from PyQt6.QtCore import Qt, QRectF, QPointF
from PyQt6.QtGui import (
    QPainter, QColor, QPen, QFont, QBrush,
    QLinearGradient, QRadialGradient,
)

NUM_STRINGS = 6
NUM_FRETS = 24
STRING_NAMES = ['e', 'B', 'G', 'D', 'A', 'E']
SINGLE_MARKERS = {3, 5, 7, 9, 15, 17, 19, 21}
DOUBLE_MARKERS = {12, 24}

ML = 60    # left margin: label + open-note area
MR = 20
MT = 28    # top: fret number row
MB = 12


class FretboardWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.active_notes: dict[int, int] = {}
        self.setMinimumSize(900, 160)

    def set_notes(self, notes: dict):
        self.active_notes = notes
        self.update()

    # ------------------------------------------------------------------ paint

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        w, h = self.width(), self.height()
        fb_w = w - ML - MR
        fb_h = h - MT - MB
        fret_w = fb_w / NUM_FRETS
        str_gap = fb_h / (NUM_STRINGS - 1)

        self._draw_background(p, w, h)
        self._draw_markers(p, fret_w, str_gap)
        self._draw_fret_wires(p, fret_w, fb_h)
        self._draw_nut(p, fb_h)
        self._draw_strings(p, str_gap, fb_w)
        self._draw_labels(p, str_gap, fret_w)
        self._draw_active_notes(p, str_gap, fret_w)

    def _draw_background(self, p, w, h):
        grad = QLinearGradient(0, 0, 0, h)
        grad.setColorAt(0, QColor(60, 38, 14))
        grad.setColorAt(1, QColor(38, 22, 6))
        p.fillRect(0, 0, w, h, grad)

    def _draw_markers(self, p, fret_w, str_gap):
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(QColor(155, 135, 95, 190)))
        for fret in range(1, NUM_FRETS + 1):
            cx = ML + (fret - 0.5) * fret_w
            if fret in SINGLE_MARKERS:
                cy = MT + (NUM_STRINGS - 1) * str_gap / 2
                p.drawEllipse(QRectF(cx - 5, cy - 5, 10, 10))
            elif fret in DOUBLE_MARKERS:
                cy1 = MT + str_gap * 1.5
                cy2 = MT + str_gap * 3.5
                p.drawEllipse(QRectF(cx - 5, cy1 - 5, 10, 10))
                p.drawEllipse(QRectF(cx - 5, cy2 - 5, 10, 10))

    def _draw_fret_wires(self, p, fret_w, fb_h):
        for fret in range(1, NUM_FRETS + 1):
            x = ML + fret * fret_w
            p.setPen(QPen(QColor(185, 165, 120), 2))
            p.drawLine(QPointF(x, MT), QPointF(x, MT + fb_h))

    def _draw_nut(self, p, fb_h):
        p.setPen(QPen(QColor(235, 220, 185), 6))
        p.drawLine(QPointF(ML, MT), QPointF(ML, MT + fb_h))

    def _draw_strings(self, p, str_gap, fb_w):
        for s in range(NUM_STRINGS):
            y = MT + s * str_gap
            thickness = 1.0 + s * 0.45
            p.setPen(QPen(QColor(215, 210, 200), thickness))
            p.drawLine(QPointF(ML, y), QPointF(ML + fb_w, y))

    def _draw_labels(self, p, str_gap, fret_w):
        p.setPen(QPen(QColor(195, 175, 140)))

        name_font = QFont('Courier', 9, QFont.Weight.Bold)
        p.setFont(name_font)
        for s, name in enumerate(STRING_NAMES):
            y = MT + s * str_gap
            p.drawText(QRectF(0, y - 10, 20, 20), Qt.AlignmentFlag.AlignCenter, name)

        num_font = QFont('Courier', 8)
        p.setFont(num_font)
        p.setPen(QPen(QColor(155, 145, 125)))
        for fret in range(1, NUM_FRETS + 1):
            cx = ML + (fret - 0.5) * fret_w
            p.drawText(QRectF(cx - 12, 4, 24, 18), Qt.AlignmentFlag.AlignCenter, str(fret))

    def _draw_active_notes(self, p, str_gap, fret_w):
        for string_num, fret_num in self.active_notes.items():
            s_idx = string_num - 1
            y = MT + s_idx * str_gap
            x = ML - 30 if fret_num == 0 else ML + (fret_num - 0.5) * fret_w
            r = 11.0

            glow = QRadialGradient(x, y, r * 2.2)
            glow.setColorAt(0, QColor(255, 60, 60, 130))
            glow.setColorAt(1, QColor(255, 60, 60, 0))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QBrush(glow))
            p.drawEllipse(QRectF(x - r * 2.2, y - r * 2.2, r * 4.4, r * 4.4))

            p.setBrush(QBrush(QColor(215, 45, 45)))
            p.setPen(QPen(QColor(255, 190, 190), 1.5))
            p.drawEllipse(QRectF(x - r, y - r, r * 2, r * 2))

            p.setPen(QPen(Qt.GlobalColor.white))
            lbl_font = QFont('Courier', 8, QFont.Weight.Bold)
            p.setFont(lbl_font)
            p.drawText(QRectF(x - r, y - r, r * 2, r * 2),
                       Qt.AlignmentFlag.AlignCenter, str(fret_num))
