#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "PyGuitarPro>=0.7.2",
#   "PyQt6>=6.6.0",
#   "pygame>=2.5.0",
#   "numpy>=1.26.0",
# ]
# ///

import sys
from typing import Dict, Tuple
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget,
    QVBoxLayout, QHBoxLayout, QPushButton,
    QLabel, QFileDialog, QListWidget, QListWidgetItem,
    QMessageBox, QScrollArea, QFrame, QSlider, QSizePolicy, QStyle,
)
from PyQt6.QtCore import Qt, QTimer


class _SeekSlider(QSlider):
    """Horizontal slider that jumps to the clicked position."""

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._apply_pos(event)
            self.sliderPressed.emit()
            event.accept()
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if event.buttons() & Qt.MouseButton.LeftButton:
            self._apply_pos(event)
            event.accept()
        else:
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.sliderReleased.emit()
            event.accept()
        else:
            super().mouseReleaseEvent(event)

    def _apply_pos(self, event):
        val = QStyle.sliderValueFromPosition(
            self.minimum(), self.maximum(),
            event.position().toPoint().x(), self.width(),
        )
        self.setValue(val)

from fretboard_widget import FretboardWidget
from player import Player
from parser import load_song, parse_track


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Fretboard Viewer")
        self.setMinimumSize(960, 340)
        self._song = None
        self._player = Player()
        self._fretboards: Dict[int, Tuple[QWidget, FretboardWidget]] = {}
        self._track_events: Dict[int, Tuple[list, float]] = {}
        self._build_ui()
        self._connect()

    def _build_ui(self):
        root = QWidget()
        self.setCentralWidget(root)
        outer = QVBoxLayout(root)
        outer.setSpacing(6)
        outer.setContentsMargins(10, 10, 10, 10)

        # ── top bar ──────────────────────────────────────────────────────
        top = QHBoxLayout()
        self._open_btn = QPushButton("Open GP File…")
        top.addWidget(self._open_btn)
        top.addStretch()
        outer.addLayout(top)

        # ── middle: track panel + scroll area ────────────────────────────
        middle = QHBoxLayout()
        middle.setSpacing(8)

        # Left: track checkbox list
        track_panel = QWidget()
        track_panel.setFixedWidth(190)
        tp_layout = QVBoxLayout(track_panel)
        tp_layout.setContentsMargins(0, 0, 0, 0)
        tp_layout.setSpacing(4)
        tp_layout.addWidget(QLabel("Tracks"))
        self._track_list = QListWidget()
        self._track_list.setSelectionMode(QListWidget.SelectionMode.NoSelection)
        self._track_list.setEnabled(False)
        tp_layout.addWidget(self._track_list)
        middle.addWidget(track_panel)

        # Right: scrollable stacked fretboards
        self._fb_scroll = QScrollArea()
        self._fb_scroll.setWidgetResizable(True)
        self._fb_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._fb_container = QWidget()
        self._fb_layout = QVBoxLayout(self._fb_container)
        self._fb_layout.setSpacing(6)
        self._fb_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self._fb_scroll.setWidget(self._fb_container)
        middle.addWidget(self._fb_scroll, stretch=1)

        outer.addLayout(middle, stretch=1)

        # ── seek slider ──────────────────────────────────────────────────
        self._seek_slider = _SeekSlider(Qt.Orientation.Horizontal)
        self._seek_slider.setRange(0, 0)
        self._seek_slider.setEnabled(False)
        self._dragging = False
        outer.addWidget(self._seek_slider)

        # ── controls ─────────────────────────────────────────────────────
        ctrl = QHBoxLayout()
        self._play_btn = QPushButton("Play")
        self._stop_btn = QPushButton("Stop")
        self._pos_lbl = QLabel("0:00")
        self._pos_lbl.setAlignment(Qt.AlignmentFlag.AlignVCenter)
        self._play_btn.setEnabled(False)
        self._stop_btn.setEnabled(False)
        self._play_btn.setFixedWidth(80)
        self._stop_btn.setFixedWidth(60)

        self._speed_slider = QSlider(Qt.Orientation.Horizontal)
        self._speed_slider.setRange(25, 100)
        self._speed_slider.setValue(100)
        self._speed_slider.setTickPosition(QSlider.TickPosition.TicksBelow)
        self._speed_slider.setTickInterval(25)
        self._speed_slider.setFixedWidth(180)
        self._speed_lbl = QLabel("1.00×")
        self._speed_lbl.setFixedWidth(42)

        self._pitch_down_btn = QPushButton("−")
        self._pitch_down_btn.setFixedWidth(28)
        self._pitch_up_btn = QPushButton("+")
        self._pitch_up_btn.setFixedWidth(28)
        self._pitch_lbl = QLabel("0 st")
        self._pitch_lbl.setFixedWidth(36)
        self._pitch_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)

        ctrl.addWidget(self._play_btn)
        ctrl.addWidget(self._stop_btn)
        ctrl.addWidget(self._pos_lbl)
        ctrl.addStretch()
        ctrl.addWidget(QLabel("Pitch:"))
        ctrl.addWidget(self._pitch_down_btn)
        ctrl.addWidget(self._pitch_lbl)
        ctrl.addWidget(self._pitch_up_btn)
        ctrl.addSpacing(12)
        ctrl.addWidget(QLabel("Speed:"))
        ctrl.addWidget(self._speed_slider)
        ctrl.addWidget(self._speed_lbl)
        outer.addLayout(ctrl)

    def _connect(self):
        self._open_btn.clicked.connect(self._open_file)
        self._play_btn.clicked.connect(self._toggle_play)
        self._stop_btn.clicked.connect(self._stop)
        self._pitch_down_btn.clicked.connect(lambda: self._shift_pitch(-1))
        self._pitch_up_btn.clicked.connect(lambda: self._shift_pitch(1))
        self._track_list.itemChanged.connect(self._on_track_toggled)
        self._speed_slider.valueChanged.connect(self._on_speed)
        self._seek_slider.sliderPressed.connect(self._seek_pressed)
        self._seek_slider.sliderReleased.connect(self._seek_released)
        self._player.notes_changed.connect(self._on_notes)
        self._player.position_changed.connect(self._on_position)
        self._player.finished.connect(self._on_finished)

    # ----------------------------------------------------------------- file

    def _open_file(self, path: str = ''):
        if not path:
            path, _ = QFileDialog.getOpenFileName(
                self, "Open Guitar Pro File", "",
                "Guitar Pro (*.gp3 *.gp4 *.gp5 *.gpx *.gp)"
            )
        if not path:
            return
        try:
            self._song = load_song(path)
        except Exception as exc:
            QMessageBox.critical(self, "Parse error", str(exc))
            return

        self._player.clear_tracks()
        for frame, _ in self._fretboards.values():
            self._fb_layout.removeWidget(frame)
            frame.deleteLater()
        self._fretboards.clear()
        self._track_events.clear()

        self._track_list.blockSignals(True)
        self._track_list.clear()
        for i, t in enumerate(self._song.tracks):
            item = QListWidgetItem(f"{i + 1}: {t.name}")
            item.setData(Qt.ItemDataRole.UserRole, i)
            item.setCheckState(Qt.CheckState.Unchecked)
            item.setToolTip(t.name)
            self._track_list.addItem(item)
        self._track_list.blockSignals(False)
        self._track_list.setEnabled(True)

        self._play_btn.setEnabled(True)
        self._stop_btn.setEnabled(True)
        self._seek_slider.setEnabled(True)
        self._seek_slider.setValue(0)
        self._play_btn.setText("Play")

        # Auto-select first track
        if self._track_list.count():
            self._track_list.item(0).setCheckState(Qt.CheckState.Checked)

    # ----------------------------------------------------------------- track management

    def _on_track_toggled(self, item: QListWidgetItem):
        track_idx = item.data(Qt.ItemDataRole.UserRole)
        if item.checkState() == Qt.CheckState.Checked:
            self._add_fretboard(track_idx, item.text())
        else:
            self._remove_fretboard(track_idx)

    def _add_fretboard(self, track_idx: int, name: str):
        if track_idx in self._fretboards or self._song is None:
            return
        try:
            events, tempo = parse_track(self._song, track_idx)
        except Exception as exc:
            QMessageBox.critical(self, "Track error", str(exc))
            self._track_list.blockSignals(True)
            self._track_list.item(track_idx).setCheckState(Qt.CheckState.Unchecked)
            self._track_list.blockSignals(False)
            return

        frame = QFrame()
        frame.setFrameShape(QFrame.Shape.StyledPanel)
        vbox = QVBoxLayout(frame)
        vbox.setSpacing(2)
        vbox.setContentsMargins(6, 4, 6, 6)

        lbl = QLabel(name)
        lbl.setStyleSheet("font-weight: bold; color: #c8a870; padding: 2px 0;")
        vbox.addWidget(lbl)

        fb = FretboardWidget()
        fb.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        vbox.addWidget(fb)

        insert_pos = sum(1 for idx in self._fretboards if idx < track_idx)
        self._fb_layout.insertWidget(insert_pos, frame)
        self._fretboards[track_idx] = (frame, fb)

        self._track_events[track_idx] = (events, tempo)
        self._player.load_track(track_idx, events)
        self._seek_slider.setRange(0, int(self._player.total_ms))
        QTimer.singleShot(0, self._resize_to_fit)

    def _remove_fretboard(self, track_idx: int):
        if track_idx not in self._fretboards:
            return
        frame, _ = self._fretboards.pop(track_idx)
        self._fb_layout.removeWidget(frame)
        frame.deleteLater()
        self._track_events.pop(track_idx, None)
        self._player.remove_track(track_idx)
        self._seek_slider.setRange(0, int(self._player.total_ms))
        QTimer.singleShot(0, self._resize_to_fit)

    def _resize_to_fit(self):
        screen = QApplication.primaryScreen().availableGeometry()
        content_h = self._fb_container.sizeHint().height()
        other_h = self.height() - self._fb_scroll.height()
        ideal = max(other_h + content_h, self.minimumHeight())
        ideal = min(ideal, screen.height())
        y = screen.top() + (screen.height() - ideal) // 2
        self.resize(self.width(), ideal)
        self.move(self.x(), y)

    # ----------------------------------------------------------------- playback

    def _toggle_play(self):
        if self._player.is_playing:
            self._player.pause()
            self._play_btn.setText("Play")
        else:
            self._player.play()
            self._play_btn.setText("Pause")

    def _stop(self):
        self._player.reset()
        self._play_btn.setText("Play")

    def _on_notes(self, track_notes: dict):
        for track_idx, notes in track_notes.items():
            if track_idx in self._fretboards:
                self._fretboards[track_idx][1].set_notes(notes)

    def _on_position(self, ms: float):
        s = int(ms / 1000)
        self._pos_lbl.setText(f"{s // 60}:{s % 60:02d}")
        if not self._dragging:
            self._seek_slider.blockSignals(True)
            self._seek_slider.setValue(int(ms))
            self._seek_slider.blockSignals(False)
        for track_idx, (frame, fb) in self._fretboards.items():
            data = self._track_events.get(track_idx)
            if data is None:
                continue
            events, tempo = data
            window_ms = 8 * (60000.0 / tempo)  # 2 bars in 4/4
            lo, hi = ms - window_ms, ms + window_ms
            ctx = [(e.string, e.fret) for e in events if lo <= e.time_ms <= hi]
            fb.set_context_notes(ctx)

    def _on_finished(self):
        self._player.reset()
        self._play_btn.setText("Play")
        for _, fb in self._fretboards.values():
            fb.set_context_notes([])

    def _seek_pressed(self):
        self._dragging = True

    def _seek_released(self):
        self._dragging = False
        self._player.seek(float(self._seek_slider.value()))

    def _shift_pitch(self, delta: int):
        self._player.set_pitch_offset(self._player.pitch_offset + delta)
        st = self._player.pitch_offset
        self._pitch_lbl.setText(f"{st:+d} st" if st else "0 st")

    def _on_speed(self, value: int):
        speed = value / 100.0
        self._speed_lbl.setText(f"{speed:.2f}×")
        self._player.set_speed(speed)


def main():
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    win = MainWindow()
    screen = app.primaryScreen().availableGeometry()
    h = win.sizeHint().height()
    win.resize(screen.width(), h)
    win.move(screen.left(), screen.top() + (screen.height() - h) // 2)
    win.show()
    if len(sys.argv) > 1:
        QTimer.singleShot(0, lambda: win._open_file(sys.argv[1]))
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
