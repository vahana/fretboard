from PyQt6.QtWidgets import QWidget
from PyQt6.QtCore import Qt, QRectF, QPointF
from PyQt6.QtGui import (
    QPainter, QColor, QPen, QFont, QBrush,
    QLinearGradient, QRadialGradient, QPainterPath,
)

NUM_STRINGS = 6
NUM_FRETS = 24
STRING_NAMES = ['e', 'B', 'G', 'D', 'A', 'E']
SINGLE_MARKERS = {3, 5, 7, 9, 15, 17, 19, 21}
DOUBLE_MARKERS = {12, 24}

ML = 60
MR = 20
MT = 28
MB = 12

_COLOR_BEND_ARROW  = QColor(255, 210, 80)
_COLOR_SLIDE       = QColor(150, 220, 150)
_COLOR_VIBRATO_TXT = QColor(180, 200, 255)


class FretboardWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.active_notes: dict = {}
        self.context_notes: list = []
        self.setMinimumSize(900, 160)

    def set_notes(self, notes: dict):
        self.active_notes = notes
        self.update()

    def set_context_notes(self, notes: list):
        self.context_notes = notes
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
        self._draw_context_notes(p, str_gap, fret_w)
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

    def _draw_context_notes(self, p, str_gap, fret_w):
        r = 9.0
        lbl_font = QFont('Courier', 7)
        p.setFont(lbl_font)
        for entry in self.context_notes:
            string_num, fret_num, is_upcoming = entry if len(entry) == 3 else (*entry, False)
            s_idx = string_num - 1
            if not (0 <= s_idx < NUM_STRINGS):
                continue
            y = MT + s_idx * str_gap
            x = ML - 30 if fret_num == 0 else ML + (fret_num - 0.5) * fret_w
            if is_upcoming:
                stroke = QColor(60,  160,  50, 220)
                text_c = QColor(80,  190,  70, 230)
            else:
                stroke = QColor(230, 205, 155, 210)
                text_c = QColor(235, 210, 160, 230)
            p.setPen(QPen(stroke, 1.5))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(QRectF(x - r, y - r, r * 2, r * 2))
            p.setPen(QPen(text_c))
            p.drawText(QRectF(x - r, y - r, r * 2, r * 2),
                       Qt.AlignmentFlag.AlignCenter, str(fret_num))

    def _draw_active_notes(self, p, str_gap, fret_w):
        for string_num, note_data in self.active_notes.items():
            if isinstance(note_data, tuple):
                fret_num, fx, bend_now = note_data
            else:
                fret_num, fx, bend_now = note_data, None, 0.0

            s_idx = string_num - 1
            y = MT + s_idx * str_gap
            x = ML - 30 if fret_num == 0 else ML + (fret_num - 0.5) * fret_w
            r = 11.0

            is_palm = fx and fx.palm_mute
            is_hammer = fx and fx.hammer_on

            # 1. slide indicators (behind dot)
            if fx and (fx.slide_in or fx.slide_out):
                self._draw_slide_lines(p, x, y, r, fx)

            # 2. glow (skip for hammer-on)
            if not is_hammer:
                if is_palm:
                    glow = QRadialGradient(x, y, r * 2.2)
                    glow.setColorAt(0, QColor(160, 110, 50, 120))
                    glow.setColorAt(1, QColor(160, 110, 50, 0))
                else:
                    glow = QRadialGradient(x, y, r * 2.2)
                    glow.setColorAt(0, QColor(255, 60, 60, 130))
                    glow.setColorAt(1, QColor(255, 60, 60, 0))
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QBrush(glow))
                p.drawEllipse(QRectF(x - r * 2.2, y - r * 2.2, r * 4.4, r * 4.4))

            # 3. dot
            if is_palm:
                p.setBrush(QBrush(QColor(100, 82, 55)))
                p.setPen(QPen(QColor(170, 140, 85), 1.5))
            elif is_hammer:
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.setPen(QPen(QColor(215, 45, 45), 2.0))
            else:
                p.setBrush(QBrush(QColor(215, 45, 45)))
                p.setPen(QPen(QColor(255, 190, 190), 1.5))
            p.drawEllipse(QRectF(x - r, y - r, r * 2, r * 2))

            # 4. label
            lbl_font = QFont('Courier', 8, QFont.Weight.Bold)
            p.setFont(lbl_font)
            p.setPen(QPen(Qt.GlobalColor.white))
            label = 'PM' if is_palm else str(fret_num)
            p.drawText(QRectF(x - r, y - r, r * 2, r * 2), Qt.AlignmentFlag.AlignCenter, label)

            # 5. bend arrow
            if abs(bend_now) > 0.05:
                self._draw_bend_arrow(p, x, y, r, bend_now)

            # 6. vibrato marker
            if fx and fx.vibrato:
                vib_font = QFont('Courier', 10, QFont.Weight.Bold)
                p.setFont(vib_font)
                p.setPen(QPen(_COLOR_VIBRATO_TXT))
                p.drawText(QRectF(x - 10, y - r - 18, 20, 14),
                           Qt.AlignmentFlag.AlignCenter, '~')

    def _draw_bend_arrow(self, p, x, y, r, bend_semitones):
        arrow_len = abs(bend_semitones) * 11.0
        if arrow_len < 3:
            return

        going_up = bend_semitones > 0
        p.setPen(QPen(_COLOR_BEND_ARROW, 1.8))
        p.setBrush(QBrush(_COLOR_BEND_ARROW))

        if going_up:
            base_y = y - r - 2
            tip_y = base_y - arrow_len
            p.drawLine(QPointF(x, base_y), QPointF(x, tip_y + 6))
            head = QPainterPath()
            head.moveTo(x, tip_y)
            head.lineTo(x - 4, tip_y + 7)
            head.lineTo(x + 4, tip_y + 7)
            head.closeSubpath()
        else:
            base_y = y + r + 2
            tip_y = base_y + arrow_len
            p.drawLine(QPointF(x, base_y), QPointF(x, tip_y - 6))
            head = QPainterPath()
            head.moveTo(x, tip_y)
            head.lineTo(x - 4, tip_y - 7)
            head.lineTo(x + 4, tip_y - 7)
            head.closeSubpath()

        p.setPen(Qt.PenStyle.NoPen)
        p.fillPath(head, QBrush(_COLOR_BEND_ARROW))

    def _draw_slide_lines(self, p, x, y, r, fx):
        p.setPen(QPen(_COLOR_SLIDE, 1.8))
        if fx.slide_in:
            dy = -15 if fx.slide_in > 0 else 15
            p.drawLine(QPointF(x - r - 15, y + dy), QPointF(x - r, y))
        if fx.slide_out:
            dy = -15 if fx.slide_out > 0 else 15
            p.drawLine(QPointF(x + r, y), QPointF(x + r + 15, y + dy))
