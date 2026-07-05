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
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget,
    QVBoxLayout, QHBoxLayout, QPushButton,
    QLabel, QFileDialog, QComboBox,
    QMessageBox, QSizePolicy, QSlider,
)
from PyQt6.QtCore import Qt

from fretboard_widget import FretboardWidget
from player import Player
from parser import load_song, parse_track


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Fretboard Viewer")
        self.setMinimumSize(960, 300)
        self._song = None
        self._player = Player()
        self._build_ui()
        self._connect()

    def _build_ui(self):
        root = QWidget()
        self.setCentralWidget(root)
        vbox = QVBoxLayout(root)
        vbox.setSpacing(8)
        vbox.setContentsMargins(10, 10, 10, 10)

        top = QHBoxLayout()
        self._open_btn = QPushButton("Open GP File…")
        self._track_combo = QComboBox()
        self._track_combo.setEnabled(False)
        self._track_combo.setMinimumWidth(220)
        top.addWidget(self._open_btn)
        top.addWidget(QLabel("Track:"))
        top.addWidget(self._track_combo)
        top.addStretch()
        vbox.addLayout(top)

        self._fb = FretboardWidget()
        self._fb.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        vbox.addWidget(self._fb)

        self._seek_slider = QSlider(Qt.Orientation.Horizontal)
        self._seek_slider.setRange(0, 0)
        self._seek_slider.setEnabled(False)
        self._dragging = False
        vbox.addWidget(self._seek_slider)

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
        self._speed_slider.setRange(25, 100)   # 0.25x – 1.00x (units: speed * 100)
        self._speed_slider.setValue(100)
        self._speed_slider.setTickPosition(QSlider.TickPosition.TicksBelow)
        self._speed_slider.setTickInterval(25)
        self._speed_slider.setFixedWidth(180)
        self._speed_lbl = QLabel("1.00×")
        self._speed_lbl.setFixedWidth(42)

        ctrl.addWidget(self._play_btn)
        ctrl.addWidget(self._stop_btn)
        ctrl.addWidget(self._pos_lbl)
        ctrl.addStretch()
        ctrl.addWidget(QLabel("Speed:"))
        ctrl.addWidget(self._speed_slider)
        ctrl.addWidget(self._speed_lbl)
        vbox.addLayout(ctrl)

    def _connect(self):
        self._open_btn.clicked.connect(self._open_file)
        self._play_btn.clicked.connect(self._toggle_play)
        self._stop_btn.clicked.connect(self._stop)
        self._track_combo.currentIndexChanged.connect(self._switch_track)
        self._player.notes_changed.connect(self._fb.set_notes)
        self._player.position_changed.connect(self._on_position)
        self._player.finished.connect(self._on_finished)
        self._speed_slider.valueChanged.connect(self._on_speed)
        self._seek_slider.sliderPressed.connect(self._seek_pressed)
        self._seek_slider.sliderReleased.connect(self._seek_released)

    def _open_file(self):
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

        self._track_combo.blockSignals(True)
        self._track_combo.clear()
        for i, t in enumerate(self._song.tracks):
            self._track_combo.addItem(f"{i + 1}: {t.name}", i)
        self._track_combo.blockSignals(False)
        self._track_combo.setEnabled(True)
        self._play_btn.setEnabled(True)
        self._stop_btn.setEnabled(True)
        self._seek_slider.setEnabled(True)
        self._load_track(0)

    def _switch_track(self, combo_idx: int):
        if self._song is None:
            return
        track_idx = self._track_combo.itemData(combo_idx)
        if track_idx is None:
            return
        self._load_track(track_idx)

    def _load_track(self, track_idx: int):
        try:
            events, tempo = parse_track(self._song, track_idx)
        except Exception as exc:
            QMessageBox.critical(self, "Track error", str(exc))
            return
        if self._player.is_playing:
            self._player.switch_events(events)
        else:
            self._player.load(events, tempo)
            self._seek_slider.setValue(0)
            self._play_btn.setText("Play")
        self._seek_slider.setRange(0, int(self._player.total_ms))

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

    def _seek_pressed(self):
        self._dragging = True

    def _seek_released(self):
        self._dragging = False
        self._player.seek(float(self._seek_slider.value()))

    def _on_position(self, ms: float):
        s = int(ms / 1000)
        self._pos_lbl.setText(f"{s // 60}:{s % 60:02d}")
        if not self._dragging:
            self._seek_slider.blockSignals(True)
            self._seek_slider.setValue(int(ms))
            self._seek_slider.blockSignals(False)

    def _on_speed(self, value: int):
        speed = value / 100.0
        self._speed_lbl.setText(f"{speed:.2f}×")
        self._player.set_speed(speed)

    def _on_finished(self):
        self._player.reset()
        self._play_btn.setText("Play")


if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    win = MainWindow()
    screen = app.primaryScreen().availableGeometry()
    h = win.sizeHint().height()
    win.resize(screen.width(), h)
    win.move(screen.left(), screen.top() + (screen.height() - h) // 2)
    win.show()
    sys.exit(app.exec())
