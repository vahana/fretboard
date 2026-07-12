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

import html.parser
import json
import re
import sys
import tempfile
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Dict, Tuple
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget,
    QVBoxLayout, QHBoxLayout, QPushButton,
    QLabel, QFileDialog, QListWidget, QListWidgetItem,
    QMessageBox, QScrollArea, QFrame, QSlider, QSizePolicy, QStyle,
    QMenu, QCheckBox, QDialog, QLineEdit, QTreeWidget, QTreeWidgetItem,
    QToolTip,
)
from PyQt6.QtCore import Qt, QTimer, QSize, QRectF, QThread, pyqtSignal
from PyQt6.QtGui import QPainter, QColor, QPen, QBrush, QShortcut, QKeySequence

_PREFS_PATH = Path.home() / ".fretboard.json"
_MAX_RECENT = 5
_GPROTAB_BASE = "https://gprotab.net"


class _TabLinkParser(html.parser.HTMLParser):
    """Extracts /en/tabs/artist/song links from a gprotab.net search page."""
    def __init__(self):
        super().__init__()
        self.results = []
        self._cur_href = None
        self._cur_text = []
        self._seen = set()

    def handle_starttag(self, tag, attrs):
        if tag != 'a':
            return
        href = dict(attrs).get('href', '')
        parts = [p for p in href.split('/') if p]
        if len(parts) == 4 and parts[0] == 'en' and parts[1] == 'tabs':
            self._cur_href = href
            self._cur_text = []

    def handle_endtag(self, tag):
        if tag == 'a' and self._cur_href is not None:
            text = ''.join(self._cur_text).strip()
            if self._cur_href not in self._seen:
                self._seen.add(self._cur_href)
                self.results.append((self._cur_href, text))
            self._cur_href = None

    def handle_data(self, data):
        if self._cur_href is not None:
            self._cur_text.append(data)


def _slug_to_title(slug: str) -> str:
    return ' '.join(w.capitalize() for w in slug.replace('-', ' ').split())


def _group_label(label: str) -> str:
    if ' – ' in label:
        artist, song = label.split(' – ', 1)
        song = re.sub(r'\s+\d+$', '', song).strip()
        return f"{artist} – {song}"
    return re.sub(r'\s+\d+$', '', label).strip()


class _SearchWorker(QThread):
    results_ready = pyqtSignal(list, str)
    error = pyqtSignal(str)

    def __init__(self, query: str, page: int = 1):
        super().__init__()
        self._query = query
        self._page = page

    def run(self):
        try:
            q = urllib.parse.quote(self._query)
            if self._page == 1:
                url = f"{_GPROTAB_BASE}/en/search/?q={q}"
            else:
                url = f"{_GPROTAB_BASE}/en/search/?q={q}&page={self._page}"
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req, timeout=10) as resp:
                html_text = resp.read().decode('utf-8', errors='replace')
            parser = _TabLinkParser()
            parser.feed(html_text)
            seen = {path for path, _ in parser.results}
            results = []
            for path, text in parser.results:
                parts = [p for p in path.split('/') if p]
                artist = _slug_to_title(parts[2])
                song = text if text else _slug_to_title(parts[3])
                results.append((path, f"{artist} – {song}"))

            if self._page == 1:
                slug = self._query.lower().strip().replace(' ', '-')
                try:
                    artist_req = urllib.request.Request(
                        f"{_GPROTAB_BASE}/en/tabs/{slug}",
                        headers={'User-Agent': 'Mozilla/5.0'},
                    )
                    with urllib.request.urlopen(artist_req, timeout=10) as resp:
                        artist_html = resp.read().decode('utf-8', errors='replace')
                    artist_parser = _TabLinkParser()
                    artist_parser.feed(artist_html)
                    for path, text in artist_parser.results:
                        if path in seen:
                            continue
                        seen.add(path)
                        parts = [p for p in path.split('/') if p]
                        song = text if text else _slug_to_title(parts[3])
                        results.append((path, f"{_slug_to_title(slug)} – {song}"))
                except Exception:
                    pass

            page_nums = [int(m) for m in re.findall(r'/en/search/[^"]*page=(\d+)', html_text)]
            next_url = f"{_GPROTAB_BASE}/en/search/?q={q}&page={self._page + 1}" if any(p > self._page for p in page_nums) else ""
            self.results_ready.emit(results, next_url)
        except Exception as exc:
            self.error.emit(str(exc))


class _RatingWorker(QThread):
    rating_ready = pyqtSignal(str)

    def __init__(self, path: str):
        super().__init__()
        self._path = path

    def run(self):
        try:
            url = f"{_GPROTAB_BASE}{self._path}"
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req, timeout=8) as resp:
                text = resp.read().decode('utf-8', errors='replace')
            m = re.search(r'(\d+(?:\.\d+)?)/5[^0-9\n]{0,40}?(\d+)\s+vote', text)
            if m:
                self.rating_ready.emit(f"{m.group(1)}/5 · {m.group(2)} votes")
            else:
                self.rating_ready.emit("no rating")
        except Exception:
            self.rating_ready.emit("")


class _DownloadWorker(QThread):
    done = pyqtSignal(str)
    error = pyqtSignal(str)

    def __init__(self, path: str, dest_dir: Path):
        super().__init__()
        self._path = path
        self._dest_dir = dest_dir

    def run(self):
        try:
            url = f"{_GPROTAB_BASE}{self._path}?download"
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req, timeout=30) as resp:
                content_disp = resp.headers.get('Content-Disposition', '')
                filename = None
                m = re.search(r'filename=["\']?([^"\';\r\n]+)', content_disp)
                if m:
                    filename = m.group(1).strip()
                if not filename:
                    filename = self._path.split('/')[-1] + '.gp5'
                data = resp.read()
            self._dest_dir.mkdir(parents=True, exist_ok=True)
            dest = self._dest_dir / filename
            dest.write_bytes(data)
            self.done.emit(str(dest))
        except Exception as exc:
            self.error.emit(str(exc))


class _GProTabDialog(QDialog):
    def __init__(self, prefs: dict, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Search GProTab")
        self.setMinimumWidth(520)
        self.setMinimumHeight(420)
        self._prefs = prefs
        self._results = []
        self._groups = {}
        self._next_url = ""
        self._query = ""
        self._next_page = 1
        self._search_worker = None
        self._rating_worker = None
        self._dl_worker = None
        self._downloaded_path = ''
        self._build()

    def _build(self):
        layout = QVBoxLayout(self)

        row = QHBoxLayout()
        self._search_edit = QLineEdit()
        self._search_edit.setPlaceholderText("Artist or song…")
        self._search_btn = QPushButton("Search")
        row.addWidget(self._search_edit)
        row.addWidget(self._search_btn)
        layout.addLayout(row)

        self._tree = QTreeWidget()
        self._tree.setHeaderHidden(True)
        self._tree.setRootIsDecorated(True)
        layout.addWidget(self._tree, stretch=1)

        self._load_more_btn = QPushButton("Load More")
        self._load_more_btn.setVisible(False)
        layout.addWidget(self._load_more_btn)

        self._status = QLabel("")
        layout.addWidget(self._status)

        dir_row = QHBoxLayout()
        dir_row.addWidget(QLabel("Save to:"))
        self._dir_lbl = QLabel(self._download_dir_str())
        self._dir_lbl.setStyleSheet("color: #888;")
        dir_row.addWidget(self._dir_lbl, stretch=1)
        self._dir_btn = QPushButton("Change…")
        self._dir_btn.setFixedWidth(70)
        dir_row.addWidget(self._dir_btn)
        layout.addLayout(dir_row)

        self._open_btn = QPushButton("Download && Open")
        self._open_btn.setEnabled(False)
        layout.addWidget(self._open_btn)

        self._search_btn.clicked.connect(self._do_search)
        self._search_edit.returnPressed.connect(self._do_search)
        self._tree.itemSelectionChanged.connect(self._on_selection)
        self._tree.itemClicked.connect(self._on_item_clicked)
        self._tree.itemDoubleClicked.connect(self._on_item_double_clicked)
        self._open_btn.clicked.connect(self._do_download)
        self._load_more_btn.clicked.connect(self._do_load_more)
        self._dir_btn.clicked.connect(self._change_dir)

    def _download_dir_str(self) -> str:
        return self._prefs.get("download_dir", str(Path.home() / "Downloads"))

    def _change_dir(self):
        chosen = QFileDialog.getExistingDirectory(self, "Select Download Folder", self._download_dir_str())
        if chosen:
            self._prefs["download_dir"] = chosen
            self._dir_lbl.setText(chosen)

    def _do_search(self):
        q = self._search_edit.text().strip()
        if not q:
            return
        self._query = q
        self._next_page = 1
        self._tree.clear()
        self._results = []
        self._groups = {}
        self._load_more_btn.setVisible(False)
        self._open_btn.setEnabled(False)
        self._status.setText("Searching…")
        self._search_btn.setEnabled(False)
        self._search_worker = _SearchWorker(q, 1)
        self._search_worker.results_ready.connect(self._on_results)
        self._search_worker.error.connect(self._on_error)
        self._search_worker.start()

    def _do_load_more(self):
        self._load_more_btn.setEnabled(False)
        self._status.setText("Loading…")
        self._search_worker = _SearchWorker(self._query, self._next_page)
        self._search_worker.results_ready.connect(self._on_results)
        self._search_worker.error.connect(self._on_error)
        self._search_worker.start()

    def _on_results(self, results, next_url):
        self._search_btn.setEnabled(True)
        self._load_more_btn.setEnabled(True)
        self._results.extend(results)

        for path, label in results:
            key = _group_label(label)
            if key not in self._groups:
                group = QTreeWidgetItem(self._tree)
                group.setFlags(Qt.ItemFlag.ItemIsEnabled)
                self._groups[key] = group
            group = self._groups[key]
            child = QTreeWidgetItem(group)
            child.setText(0, _slug_to_title(path.split('/')[-1]))
            child.setData(0, Qt.ItemDataRole.UserRole, path)
            count = group.childCount()
            group.setText(0, f"{key}  ({count})" if count > 1 else key)

        if not self._results:
            self._status.setText("No results.")
            self._load_more_btn.setVisible(False)
            return
        self._tree.sortItems(0, Qt.SortOrder.AscendingOrder)
        self._status.setText(f"{len(self._results)} tab(s) across {len(self._groups)} song(s)")
        if next_url:
            self._next_page += 1
            self._load_more_btn.setVisible(True)
        else:
            self._load_more_btn.setVisible(False)

    def _on_error(self, msg):
        self._search_btn.setEnabled(True)
        self._open_btn.setEnabled(bool(self._tree.selectedItems()))
        self._status.setText(f"Error: {msg}")

    def _on_selection(self):
        items = self._tree.selectedItems()
        self._open_btn.setEnabled(bool(items) and items[0].parent() is not None)

    def _on_item_clicked(self, item, column):
        if item.parent() is None:
            item.setExpanded(not item.isExpanded())
            return
        path = item.data(0, Qt.ItemDataRole.UserRole)
        label = item.text(0)
        self._status.setText(f"{label}  –  fetching rating…")
        if self._rating_worker and self._rating_worker.isRunning():
            self._rating_worker.terminate()
        self._rating_worker = _RatingWorker(path)
        self._rating_worker.rating_ready.connect(
            lambda r, lbl=label: self._on_rating_ready(r, lbl)
        )
        self._rating_worker.start()

    def _on_item_double_clicked(self, item, column):
        if item.parent() is not None:
            self._do_download()

    def _on_rating_ready(self, rating: str, label: str):
        if rating:
            self._status.setText(f"{label}  –  {rating}")
        else:
            self._status.setText(label)

    def _do_download(self):
        items = self._tree.selectedItems()
        if not items or items[0].parent() is None:
            return
        item = items[0]
        path = item.data(0, Qt.ItemDataRole.UserRole)
        label = item.text(0)
        self._status.setText(f"Downloading {label}…")
        self._open_btn.setEnabled(False)
        self._dl_worker = _DownloadWorker(path, Path(self._download_dir_str()))
        self._dl_worker.done.connect(self._on_downloaded)
        self._dl_worker.error.connect(self._on_error)
        self._dl_worker.start()

    def _on_downloaded(self, file_path: str):
        self._downloaded_path = file_path
        self.accept()

    def selected_file(self) -> str:
        return self._downloaded_path


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
from parser import load_song, parse_track, parse_beats, parse_sections


class _LoopBar(QWidget):
    seek_requested = pyqtSignal(float)
    markers_changed = pyqtSignal(list)
    segment_selected = pyqtSignal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(20)
        self._total_ms = 0.0
        self._markers: list = []
        self._active_segment = -1
        self._playhead_ms = 0.0
        self._drag_idx = -1
        self._loop_active = False
        self._beat_times: list = []
        self._bar_times: list = []
        self._sections: list = []  # [(time_ms, title), ...]
        self._seg_colors = [
            (QColor(35, 70, 110), QColor(60, 110, 170)),
            (QColor(70, 40, 100), QColor(110, 70, 155)),
            (QColor(35, 90, 65), QColor(60, 140, 100)),
            (QColor(100, 65, 25), QColor(155, 105, 45)),
        ]
        self.setMouseTracking(True)

    def set_loop_active(self, on: bool):
        self._loop_active = on
        self.update()

    def set_sections(self, sections: list):
        self._sections = sections
        self.update()

    def set_beats(self, beat_times: list, bar_times: list):
        self._beat_times = beat_times
        self._bar_times = bar_times
        self.update()

    def _bar_num_at(self, ms: float) -> int:
        if not self._bar_times:
            return 0
        for i in range(len(self._bar_times) - 1, -1, -1):
            if self._bar_times[i] <= ms:
                return i + 1
        return 1

    def _snap(self, ms: float) -> float:
        if not self._bar_times:
            return ms
        return min(self._bar_times, key=lambda t: abs(t - ms))

    def set_total(self, ms: float):
        self._total_ms = ms
        self.update()

    def set_playhead(self, ms: float):
        self._playhead_ms = ms
        self.update()

    def set_markers(self, markers: list):
        self._markers = sorted(markers)
        self.update()

    def set_active_segment(self, idx: int):
        self._active_segment = idx
        self.update()

    def get_segment_bounds(self, idx: int):
        if idx < 0:
            return None
        all_m = [0.0] + self._markers + [self._total_ms]
        if idx >= len(all_m) - 1:
            return None
        return all_m[idx], all_m[idx + 1]

    def _ms_to_x(self, ms: float) -> float:
        if self._total_ms <= 0:
            return 0.0
        return ms / self._total_ms * self.width()

    def _x_to_ms(self, x: float) -> float:
        if self._total_ms <= 0:
            return 0.0
        return max(0.0, min(self._total_ms, x / self.width() * self._total_ms))

    def _marker_near(self, x: float) -> int:
        for i, m in enumerate(self._markers):
            if abs(self._ms_to_x(m) - x) <= 12:
                return i
        return -1

    def _segment_at_x(self, x: float) -> int:
        ms = self._x_to_ms(x)
        all_m = [0.0] + self._markers + [self._total_ms]
        for i in range(len(all_m) - 1):
            if all_m[i] <= ms < all_m[i + 1]:
                return i
        return max(0, len(all_m) - 2)

    def mousePressEvent(self, event):
        if self._total_ms <= 0:
            return
        x = event.position().x()
        i = self._marker_near(x)
        if i >= 0:
            self._drag_idx = i
            return
        if event.button() == Qt.MouseButton.LeftButton:
            seg = self._segment_at_x(x)
            self._active_segment = seg
            all_m = [0.0] + self._markers + [self._total_ms]
            self.seek_requested.emit(all_m[seg])
            self.segment_selected.emit(seg)
            self.update()

    def mouseDoubleClickEvent(self, event):
        if self._total_ms <= 0 or self._drag_idx >= 0:
            return
        if event.button() == Qt.MouseButton.LeftButton:
            x = event.position().x()
            i = self._marker_near(x)
            if i >= 0:
                self._markers.pop(i)
                n_segs = len(self._markers) + 1
                if self._active_segment >= n_segs:
                    self._active_segment = n_segs - 1
                self.markers_changed.emit(list(self._markers))
                self.update()
            else:
                ms = self._snap(self._x_to_ms(x))
                if ms == 0.0 or ms in self._markers or ms >= self._total_ms:
                    return
                self._markers.append(ms)
                self._markers.sort()
                self.markers_changed.emit(list(self._markers))
                self.update()

    def mouseMoveEvent(self, event):
        if self._bar_times and self._total_ms > 0:
            ms = self._x_to_ms(event.position().x())
            bar = self._bar_num_at(ms)
            section = next(
                (title for t, title in reversed(self._sections) if t <= ms),
                None,
            )
            tip = f"Bar {bar}  –  {section}" if section else f"Bar {bar}"
            QToolTip.showText(event.globalPosition().toPoint(), tip, self)
        if self._drag_idx >= 0 and event.buttons() & Qt.MouseButton.LeftButton:
            i = self._drag_idx
            lo = self._markers[i - 1] if i > 0 else 0.0
            hi = self._markers[i + 1] if i < len(self._markers) - 1 else self._total_ms
            valid = [t for t in self._bar_times if lo < t < hi]
            if not valid:
                return
            ms_raw = self._x_to_ms(event.position().x())
            self._markers[i] = min(valid, key=lambda t: abs(t - ms_raw))
            self.markers_changed.emit(list(self._markers))
            self.update()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_idx = -1

    def contextMenuEvent(self, event):
        if self._total_ms <= 0:
            return
        i = self._marker_near(event.pos().x())
        if i < 0:
            return
        menu = QMenu(self)
        act = menu.addAction("Remove marker")
        if menu.exec(event.globalPosition().toPoint()) == act:
            self._markers.pop(i)
            n_segs = len(self._markers) + 1
            if self._active_segment >= n_segs:
                self._active_segment = n_segs - 1
            self.markers_changed.emit(list(self._markers))
            self.update()

    def paintEvent(self, _event):
        if self._total_ms <= 0:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        all_m = [0.0] + self._markers + [self._total_ms]

        p.fillRect(0, 0, w, h, QColor(28, 28, 28))

        for i in range(len(all_m) - 1):
            x0 = int(self._ms_to_x(all_m[i]))
            x1 = int(self._ms_to_x(all_m[i + 1]))
            inactive, active = self._seg_colors[i % len(self._seg_colors)]
            p.fillRect(x0 + 1, 1, x1 - x0 - 1, h - 2,
                       active if i == self._active_segment else inactive)

        if not self._loop_active:
            p.fillRect(0, 1, w, h - 2, QColor(0, 0, 0, 110))

        # beat grid
        if self._beat_times and self._total_ms > 0:
            px_per_beat = w / self._total_ms * (
                self._beat_times[1] - self._beat_times[0]
                if len(self._beat_times) > 1 else self._total_ms
            )
            if px_per_beat >= 3:
                p.setPen(QPen(QColor(255, 255, 255, 30), 1))
                for t in self._beat_times:
                    p.drawLine(int(self._ms_to_x(t)), h - 4, int(self._ms_to_x(t)), h)
            if px_per_beat >= 2 and self._bar_times:
                p.setPen(QPen(QColor(255, 255, 255, 70), 1))
                for t in self._bar_times:
                    p.drawLine(int(self._ms_to_x(t)), h - 7, int(self._ms_to_x(t)), h)

        # user markers
        p.setPen(QPen(QColor(200, 200, 200, 200), 1))
        for m in self._markers:
            x = int(self._ms_to_x(m))
            p.drawLine(x, 0, x, h)

        # section dots
        if self._sections:
            for t, _title in self._sections:
                x = int(self._ms_to_x(t))
                p.setPen(QPen(QColor(0, 0, 0, 160), 1))
                p.setBrush(QBrush(QColor(255, 215, 60)))
                p.drawEllipse(x - 4, h // 2 - 4, 8, 8)

        ph_x = int(self._ms_to_x(self._playhead_ms))
        p.setPen(QPen(QColor(255, 60, 60), 1))
        p.drawLine(ph_x, 0, ph_x, h)


class _BeatIndicator(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._beat = 0
        self._total = 4
        self._flash = False
        self._flash_timer = QTimer(self)
        self._flash_timer.setSingleShot(True)
        self._flash_timer.timeout.connect(self._dim)
        self.setFixedHeight(24)
        self.setFixedWidth(90)

    def set_beat(self, beat_num: int, beats_per_bar: int):
        self._beat = beat_num
        self._total = beats_per_bar
        self._flash = True
        self._flash_timer.start(90)
        self.setFixedWidth(max(beats_per_bar * 20 + 10, 50))
        self.update()

    def reset(self):
        self._beat = 0
        self._flash = False
        self.update()

    def _dim(self):
        self._flash = False
        self.update()

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = 7.0
        spacing = 18
        cy = self.height() / 2
        x0 = (self.width() - self._total * spacing) / 2 + r
        for i in range(self._total):
            cx = x0 + i * spacing
            active = (i + 1) == self._beat and self._flash
            is_down = (i == 0)
            if active:
                col = QColor(255, 90, 60) if is_down else QColor(255, 200, 60)
                p.setBrush(QBrush(col))
                p.setPen(Qt.PenStyle.NoPen)
            else:
                p.setBrush(Qt.BrushStyle.NoBrush)
                col = QColor(160, 70, 50) if is_down else QColor(120, 110, 70)
                p.setPen(QPen(col, 1.5))
            p.drawEllipse(QRectF(cx - r, cy - r, r * 2, r * 2))


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Fretboard Viewer")
        self.setMinimumSize(960, 340)
        self._song = None
        self._current_path: str | None = None
        self._player = Player()
        self._fretboards: Dict[int, Tuple[QWidget, FretboardWidget]] = {}
        self._track_events: Dict[int, Tuple[list, float]] = {}
        self._track_rows: Dict[int, Tuple[QCheckBox, QPushButton]] = {}
        self._load_prefs()
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
        self._recent_btn = QPushButton("Recent ▾")
        self._recent_btn.setFixedWidth(90)
        self._recent_menu = QMenu(self)
        self._recent_btn.setMenu(self._recent_menu)
        top.addWidget(self._recent_btn)
        self._gprotab_btn = QPushButton("Search GProTab…")
        top.addWidget(self._gprotab_btn)
        top.addStretch()
        self._help_btn = QPushButton("?")
        self._help_btn.setFixedWidth(28)
        top.addWidget(self._help_btn)
        outer.addLayout(top)
        self._refresh_recent_menu()

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

        # ── loop bar ─────────────────────────────────────────────────────
        self._loop_bar = _LoopBar()
        self._loop_bar.setEnabled(False)
        outer.addWidget(self._loop_bar)

        # ── controls ─────────────────────────────────────────────────────
        ctrl = QHBoxLayout()
        self._play_btn = QPushButton("Play")
        self._stop_btn = QPushButton("Stop")
        self._pos_lbl = QLabel("0:00")
        self._pos_lbl.setAlignment(Qt.AlignmentFlag.AlignVCenter)
        self._bar_lbl = QLabel("")
        self._bar_lbl.setAlignment(Qt.AlignmentFlag.AlignVCenter)
        self._bar_lbl.setStyleSheet("color: #888;")
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

        self._metro_btn = QPushButton("Metro")
        self._metro_btn.setCheckable(True)
        self._metro_btn.setFixedWidth(56)
        self._beat_indicator = _BeatIndicator()

        self._loop_btn = QPushButton("Loop")
        self._loop_btn.setCheckable(True)
        self._loop_btn.setFixedWidth(48)

        ctrl.addWidget(self._play_btn)
        ctrl.addWidget(self._stop_btn)
        ctrl.addWidget(self._pos_lbl)
        ctrl.addSpacing(8)
        ctrl.addWidget(self._metro_btn)
        ctrl.addWidget(self._beat_indicator)
        ctrl.addSpacing(8)
        ctrl.addWidget(self._loop_btn)
        ctrl.addStretch()
        ctrl.addWidget(self._bar_lbl)
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
        self._gprotab_btn.clicked.connect(self._open_gprotab)
        self._help_btn.clicked.connect(self._show_help)
        self._play_btn.clicked.connect(self._toggle_play)
        self._stop_btn.clicked.connect(self._stop)
        self._pitch_down_btn.clicked.connect(lambda: self._shift_pitch(-1))
        self._pitch_up_btn.clicked.connect(lambda: self._shift_pitch(1))
        self._speed_slider.valueChanged.connect(self._on_speed)
        self._seek_slider.sliderPressed.connect(self._seek_pressed)
        self._seek_slider.sliderReleased.connect(self._seek_released)
        self._metro_btn.toggled.connect(self._on_metro_toggled)
        self._loop_btn.toggled.connect(self._on_loop_toggled)
        self._loop_bar.seek_requested.connect(self._player.seek)
        self._loop_bar.segment_selected.connect(self._on_segment_selected)
        self._loop_bar.markers_changed.connect(self._on_markers_changed)
        self._player.notes_changed.connect(self._on_notes)
        self._player.position_changed.connect(self._on_position)
        self._player.finished.connect(self._on_finished)
        self._player.beat_changed.connect(self._beat_indicator.set_beat)
        for key, slot in [
            ("Space", self._toggle_play),
            ("M", self._metro_btn.toggle),
            ("L", self._loop_btn.toggle),
            ("Left", lambda: self._seek_by_bar(-1)),
            ("Right", lambda: self._seek_by_bar(1)),
            ("Ctrl+Left", lambda: self._cmd_marker_bar(-1)),
            ("Ctrl+Right", lambda: self._cmd_marker_bar(1)),
            ("D", self._delete_marker_at_current),
            ("Up", lambda: self._seek_to_segment(1)),
            ("Down", lambda: self._seek_to_segment(-1)),
        ]:
            sc = QShortcut(QKeySequence(key), self)
            sc.setContext(Qt.ShortcutContext.ApplicationShortcut)
            sc.activated.connect(slot)

    # ----------------------------------------------------------------- file

    def _show_help(self):
        dlg = QDialog(self)
        dlg.setWindowTitle("Keyboard Shortcuts")
        dlg.setMinimumWidth(380)
        layout = QVBoxLayout(dlg)
        layout.setSpacing(0)

        sections = [
            ("Playback", [
                ("Space", "Play / Pause"),
                ("M", "Toggle metronome"),
            ]),
            ("Navigation", [
                ("← →", "Move one bar back / forward"),
                ("↑ ↓", "Next / previous segment"),
            ]),
            ("Loop segments", [
                ("L", "Toggle loop on selected segment"),
                ("⌘ ← →", "Move marker at playhead one bar, or create one"),
                ("D", "Delete marker at playhead"),
                ("Double-click bar", "Add marker at that bar"),
                ("Double-click marker", "Remove that marker"),
            ]),
        ]

        for title, rows in sections:
            header = QLabel(title)
            header.setStyleSheet("font-weight: bold; padding: 10px 0 4px 0;")
            layout.addWidget(header)
            for key, desc in rows:
                row = QHBoxLayout()
                row.setContentsMargins(0, 2, 0, 2)
                key_lbl = QLabel(key)
                key_lbl.setFixedWidth(110)
                key_lbl.setStyleSheet(
                    "font-family: monospace; background: #2a2a2a; color: #ddd;"
                    "border-radius: 3px; padding: 2px 6px;"
                )
                desc_lbl = QLabel(desc)
                row.addWidget(key_lbl)
                row.addSpacing(10)
                row.addWidget(desc_lbl)
                row.addStretch()
                w = QWidget()
                w.setLayout(row)
                layout.addWidget(w)

        close_btn = QPushButton("Close")
        close_btn.clicked.connect(dlg.accept)
        layout.addSpacing(12)
        layout.addWidget(close_btn)
        dlg.exec()

    def _open_gprotab(self):
        dlg = _GProTabDialog(self._prefs, self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self._save_prefs()
            path = dlg.selected_file()
            if path:
                self._open_file(path)
        else:
            self._save_prefs()

    def _open_file(self, path: str = ''):
        if not path:
            path, _ = QFileDialog.getOpenFileName(
                self, "Open Guitar Pro File", "",
                "Guitar Pro (*.gp3 *.gp4 *.gp5 *.gpx *.gp)"
            )
        if not path:
            return

        self._save_current_state()

        try:
            self._song = load_song(path)
        except Exception as exc:
            QMessageBox.critical(self, "Parse error", str(exc))
            return

        self._player.clear_tracks()
        self._player.clear_loop()
        self._loop_btn.setChecked(False)
        self._loop_bar.set_markers([])
        self._loop_bar.set_active_segment(-1)
        self._loop_bar.set_total(0.0)
        self._loop_bar.set_beats([], [])
        self._loop_bar.set_sections([])
        for frame, _ in self._fretboards.values():
            self._fb_layout.removeWidget(frame)
            frame.deleteLater()
        self._fretboards.clear()
        self._track_events.clear()
        self._track_rows.clear()

        self._track_list.clear()
        for i, t in enumerate(self._song.tracks):
            name = f"{i + 1}: {t.name}"
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, i)
            self._track_list.addItem(item)
            row, cb, mute_btn = self._make_track_row(i, name)
            item.setSizeHint(QSize(0, 28))
            self._track_list.setItemWidget(item, row)
            self._track_rows[i] = (cb, mute_btn)
        self._track_list.setEnabled(True)

        self._play_btn.setEnabled(True)
        self._stop_btn.setEnabled(True)
        self._seek_slider.setEnabled(True)
        self._seek_slider.setValue(0)
        self._loop_bar.setEnabled(True)
        self._play_btn.setText("Play")

        _beats = parse_beats(self._song)
        self._player.load_metronome(_beats)
        self._loop_bar.set_beats(
            [b.time_ms for b in _beats],
            [b.time_ms for b in _beats if b.beat_num == 1],
        )
        self._loop_bar.set_sections(parse_sections(self._song))
        self._beat_indicator.reset()
        self._current_path = path
        self._update_recent(path)
        song_title = getattr(self._song, 'title', '') or Path(path).stem
        artist = getattr(self._song, 'artist', '')
        self.setWindowTitle(f"{artist} – {song_title}" if artist else song_title)

        state = self._prefs.get("states", {}).get(path)
        if state:
            self._restore_state(state)
        elif self._track_rows:
            self._track_rows[0][0].setChecked(True)

    # ----------------------------------------------------------------- track management

    def _make_track_row(self, idx: int, name: str):
        w = QWidget()
        h = QHBoxLayout(w)
        h.setContentsMargins(2, 1, 2, 1)
        h.setSpacing(4)
        cb = QCheckBox()
        cb.setChecked(False)
        cb.toggled.connect(lambda checked, i=idx, n=name: self._on_track_checked(i, n, checked))
        mute_btn = QPushButton("M")
        mute_btn.setCheckable(True)
        mute_btn.setFixedSize(22, 22)
        mute_btn.setToolTip("Mute")
        mute_btn.toggled.connect(lambda on, i=idx, btn=mute_btn: self._on_track_muted(i, on, btn))
        lbl = QLabel(name)
        lbl.setToolTip(name)
        h.addWidget(cb)
        h.addWidget(lbl, stretch=1)
        h.addWidget(mute_btn)
        return w, cb, mute_btn

    def _on_track_checked(self, idx: int, name: str, checked: bool):
        if checked:
            self._add_fretboard(idx, name)
        else:
            self._remove_fretboard(idx)

    def _on_track_muted(self, idx: int, muted: bool, btn: QPushButton):
        self._player.mute_track(idx, muted)
        btn.setStyleSheet("background: #8b0000; color: white;" if muted else "")

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
        self._loop_bar.set_total(self._player.total_ms)
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
        self._loop_bar.set_total(self._player.total_ms)
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
        self._beat_indicator.reset()

    def _on_notes(self, track_notes: dict):
        for track_idx, notes in track_notes.items():
            if track_idx in self._fretboards:
                self._fretboards[track_idx][1].set_notes(notes)

    def _on_position(self, ms: float):
        s = int(ms / 1000)
        self._pos_lbl.setText(f"{s // 60}:{s % 60:02d}")
        bar = self._loop_bar._bar_num_at(ms)
        section = next(
            (title for t, title in reversed(self._loop_bar._sections) if t <= ms),
            None,
        )
        self._bar_lbl.setText(
            f"{section}  –  bar {bar}" if section else (f"bar {bar}" if bar else "")
        )
        self._loop_bar.set_playhead(ms)
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
            ctx = [
                (e.string, e.fret, e.time_ms > ms, abs(e.time_ms - ms) / window_ms,
                 'dead' if (e.effects and e.effects.dead) else
                 'palm' if (e.effects and e.effects.palm_mute) else '')
                for e in events if lo <= e.time_ms <= hi
            ]
            fb.set_context_notes(ctx)

    def _on_finished(self):
        self._player.reset()
        self._play_btn.setText("Play")
        self._beat_indicator.reset()
        for _, fb in self._fretboards.values():
            fb.set_context_notes([])

    def _on_loop_toggled(self, on: bool):
        self._loop_bar.set_loop_active(on)
        if on:
            self._apply_loop()
        else:
            self._player.clear_loop()

    def _on_segment_selected(self, seg_idx: int):
        if self._loop_btn.isChecked():
            self._apply_loop()

    def _on_markers_changed(self, _markers: list):
        if self._loop_btn.isChecked():
            self._apply_loop()

    def _apply_loop(self):
        bounds = self._loop_bar.get_segment_bounds(self._loop_bar._active_segment)
        if bounds:
            self._player.set_loop(*bounds)
        else:
            self._player.clear_loop()

    def _delete_marker_at_current(self):
        bars = self._loop_bar._bar_times
        if not bars or self._player.total_ms <= 0:
            return
        now = self._player._now() if self._player.is_playing else self._player._offset_ms
        snapped = self._loop_bar._snap(now)
        markers = list(self._loop_bar._markers)
        if snapped not in markers:
            return
        markers.remove(snapped)
        n_segs = len(markers) + 1
        if self._loop_bar._active_segment >= n_segs:
            self._loop_bar.set_active_segment(n_segs - 1)
        self._loop_bar.set_markers(markers)
        if self._loop_btn.isChecked():
            self._apply_loop()

    def _cmd_marker_bar(self, delta: int):
        bars = self._loop_bar._bar_times
        if not bars or self._player.total_ms <= 0:
            return
        now = self._player._now() if self._player.is_playing else self._player._offset_ms
        snapped = self._loop_bar._snap(now)
        markers = list(self._loop_bar._markers)

        if snapped in markers:
            i = markers.index(snapped)
            lo = markers[i - 1] if i > 0 else 0.0
            hi = markers[i + 1] if i < len(markers) - 1 else self._player.total_ms
            valid = [t for t in bars if lo < t < hi and t != snapped]
            candidates = [t for t in valid if t > snapped] if delta > 0 else [t for t in valid if t < snapped]
            if not candidates:
                return
            new_ms = candidates[0] if delta > 0 else candidates[-1]
            markers[i] = new_ms
            markers.sort()
            self._loop_bar.set_markers(markers)
            self._player.seek(new_ms)
        else:
            if snapped == 0.0 or snapped in markers or snapped >= self._player.total_ms:
                return
            markers.append(snapped)
            markers.sort()
            self._loop_bar.set_markers(markers)
            self._player.seek(snapped)

        if self._loop_btn.isChecked():
            self._apply_loop()

    def _seek_to_segment(self, delta: int):
        n_segs = len(self._loop_bar._markers) + 1
        if n_segs < 2:
            return
        cur = self._loop_bar._active_segment
        if cur < 0:
            now = self._player._now() if self._player.is_playing else self._player._offset_ms
            cur = self._loop_bar._segment_at_x(self._loop_bar._ms_to_x(now))
        idx = max(0, min(n_segs - 1, cur + delta))
        if idx == cur:
            return
        self._loop_bar.set_active_segment(idx)
        bounds = self._loop_bar.get_segment_bounds(idx)
        if bounds:
            self._player.seek(bounds[0])
        if self._loop_btn.isChecked():
            self._apply_loop()

    def _seek_by_bar(self, delta: int):
        bars = self._loop_bar._bar_times
        if not bars or self._player.total_ms <= 0:
            return
        now = self._player._now() if self._player.is_playing else self._player._offset_ms
        if delta > 0:
            targets = [t for t in bars if t > now + 50]
            ms = targets[0] if targets else self._player.total_ms
        else:
            targets = [t for t in bars if t < now - 50]
            ms = targets[-1] if targets else 0.0
        self._player.seek(ms)

    def _add_marker_at_current(self):
        if self._player.total_ms <= 0:
            return
        ms = self._player._now() if self._player.is_playing else self._player._offset_ms
        ms = self._loop_bar._snap(ms)
        if ms == 0.0 or ms in self._loop_bar._markers or ms >= self._player.total_ms:
            return
        markers = list(self._loop_bar._markers)
        markers.append(ms)
        markers.sort()
        self._loop_bar.set_markers(markers)
        if self._loop_btn.isChecked():
            self._apply_loop()

    def _seek_pressed(self):
        self._dragging = True

    def _seek_released(self):
        self._dragging = False
        self._player.seek(float(self._seek_slider.value()))

    def _on_metro_toggled(self, on: bool):
        self._player.set_metronome(on)
        if not on:
            self._beat_indicator.reset()

    def _shift_pitch(self, delta: int):
        self._player.set_pitch_offset(self._player.pitch_offset + delta)
        st = self._player.pitch_offset
        self._pitch_lbl.setText(f"{st:+d} st" if st else "0 st")

    def _on_speed(self, value: int):
        speed = value / 100.0
        self._speed_lbl.setText(f"{speed:.2f}×")
        self._player.set_speed(speed)

    # ----------------------------------------------------------------- prefs / state

    def _load_prefs(self):
        try:
            self._prefs = json.loads(_PREFS_PATH.read_text())
        except Exception:
            self._prefs = {"recent": [], "states": {}}

    def _save_prefs(self):
        try:
            _PREFS_PATH.write_text(json.dumps(self._prefs, indent=2))
        except Exception:
            pass

    def _save_current_state(self):
        if self._current_path is None:
            return
        enabled = [i for i, (cb, _) in self._track_rows.items() if cb.isChecked()]
        muted = [i for i, (_, btn) in self._track_rows.items() if btn.isChecked()]
        pos = self._player._now() if self._player.is_playing else self._player._offset_ms
        self._prefs.setdefault("states", {})[self._current_path] = {
            "position_ms": pos,
            "tracks": enabled,
            "muted": muted,
            "pitch": self._player.pitch_offset,
            "speed": self._speed_slider.value(),
            "markers": list(self._loop_bar._markers),
            "active_segment": self._loop_bar._active_segment,
            "loop_enabled": self._loop_btn.isChecked(),
        }

    def _restore_state(self, state: dict):
        pitch = state.get("pitch", 0)
        self._player.set_pitch_offset(pitch)
        self._pitch_lbl.setText(f"{pitch:+d} st" if pitch else "0 st")

        self._speed_slider.setValue(state.get("speed", 100))

        tracks = set(state.get("tracks", []))
        muted = set(state.get("muted", []))
        for idx, (cb, mute_btn) in self._track_rows.items():
            if idx in tracks:
                cb.setChecked(True)
            if idx in muted:
                mute_btn.setChecked(True)

        if not tracks and self._track_rows:
            first = min(self._track_rows)
            self._track_rows[first][0].setChecked(True)

        pos = state.get("position_ms", 0.0)
        if pos > 0 and self._player.total_ms > 0:
            self._player.seek(pos)
            self._seek_slider.setValue(int(pos))
            s = int(pos / 1000)
            self._pos_lbl.setText(f"{s // 60}:{s % 60:02d}")

        markers = state.get("markers", [])
        active_seg = state.get("active_segment", -1)
        if markers:
            self._loop_bar.set_markers(markers)
        if active_seg >= 0:
            self._loop_bar.set_active_segment(active_seg)
        if state.get("loop_enabled", False):
            self._loop_btn.setChecked(True)

    def _update_recent(self, path: str):
        recents = self._prefs.setdefault("recent", [])
        if path in recents:
            recents.remove(path)
        recents.insert(0, path)
        self._prefs["recent"] = recents[:_MAX_RECENT]
        self._refresh_recent_menu()

    def _refresh_recent_menu(self):
        self._recent_menu.clear()
        recents = self._prefs.get("recent", [])
        if not recents:
            act = self._recent_menu.addAction("(none)")
            act.setEnabled(False)
            return
        for path in recents:
            p = Path(path)
            act = self._recent_menu.addAction(p.name)
            act.setToolTip(path)
            if not p.exists():
                act.setEnabled(False)
            else:
                act.triggered.connect(lambda checked, p=path: self._open_file(p))

    def closeEvent(self, event):
        self._save_current_state()
        self._save_prefs()
        super().closeEvent(event)


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
    elif win._prefs.get("recent"):
        last = win._prefs["recent"][0]
        if Path(last).exists():
            QTimer.singleShot(0, lambda: win._open_file(last))
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
