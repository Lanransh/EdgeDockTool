from __future__ import annotations

import sys
from ctypes import POINTER, Structure, WinDLL, byref, c_void_p, wintypes
from pathlib import Path
from subprocess import Popen

from PySide6.QtCore import QAbstractNativeEventFilter, QPoint, QRect, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QColor, QCursor, QDragEnterEvent, QDropEvent, QGuiApplication, QIcon, QKeyEvent, QPainter, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFrame,
    QGraphicsDropShadowEffect,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QLineEdit,
    QMenu,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSystemTrayIcon,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from .config import AppConfig, ShortcutItem, load_config, save_config
from .utils import display_name, item_kind, open_path

EDGE_LABELS = {
    "left": "左侧",
    "right": "右侧",
    "top": "顶部",
    "bottom": "底部",
}

MODE_LABELS = {
    "hover": "悬浮模式",
    "hotkey": "快捷键模式",
}

HOTKEY_ID = 1
MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
WM_HOTKEY = 0x0312

user32 = WinDLL("user32", use_last_error=True)


class MSG(Structure):
    _fields_ = [
        ("hwnd", wintypes.HWND),
        ("message", wintypes.UINT),
        ("wParam", wintypes.WPARAM),
        ("lParam", wintypes.LPARAM),
        ("time", wintypes.DWORD),
        ("pt_x", wintypes.LONG),
        ("pt_y", wintypes.LONG),
    ]


class HotkeyFilter(QAbstractNativeEventFilter):
    def __init__(self, callback):
        super().__init__()
        self.callback = callback

    def nativeEventFilter(self, event_type, message):
        if event_type != b"windows_generic_MSG":
            return False, 0
        msg = MSG.from_address(int(message))
        if msg.message == WM_HOTKEY and msg.wParam == HOTKEY_ID:
            self.callback()
            return True, 0
        return False, 0


MODIFIER_FLAGS = {
    "Ctrl": MOD_CONTROL,
    "Alt": MOD_ALT,
    "Shift": MOD_SHIFT,
}

VIRTUAL_KEYS = {
    "Space": 0x20,
    "F1": 0x70,
    "F2": 0x71,
    "F3": 0x72,
    "F4": 0x73,
    "F5": 0x74,
    "F6": 0x75,
    "F7": 0x76,
    "F8": 0x77,
    "F9": 0x78,
    "F10": 0x79,
    "F11": 0x7A,
    "F12": 0x7B,
    "A": 0x41,
    "B": 0x42,
    "C": 0x43,
    "D": 0x44,
    "E": 0x45,
    "G": 0x47,
    "H": 0x48,
    "J": 0x4A,
    "K": 0x4B,
    "L": 0x4C,
    "M": 0x4D,
    "N": 0x4E,
    "P": 0x50,
    "Q": 0x51,
    "R": 0x52,
    "T": 0x54,
    "U": 0x55,
    "V": 0x56,
    "W": 0x57,
    "X": 0x58,
    "Y": 0x59,
    "Z": 0x5A,
}


def format_hotkey(modifiers: list[str], key: str) -> str:
    return "+".join(modifiers + [key])


class HotkeyInput(QLineEdit):
    hotkey_changed = Signal(list, str)

    def __init__(self, modifiers: list[str], key: str, parent: QWidget | None = None):
        super().__init__(parent)
        self.modifiers = list(modifiers)
        self.key_name = key
        self.setReadOnly(True)
        self.setPlaceholderText("点击后按下快捷键")
        self.setText(format_hotkey(self.modifiers, self.key_name))

    def keyPressEvent(self, event: QKeyEvent):
        key = event.key()
        modifiers = []
        qt_modifiers = event.modifiers()
        if qt_modifiers & Qt.ControlModifier:
            modifiers.append("Ctrl")
        if qt_modifiers & Qt.AltModifier:
            modifiers.append("Alt")
        if qt_modifiers & Qt.ShiftModifier:
            modifiers.append("Shift")

        key_name = None
        if key == Qt.Key_Space:
            key_name = "Space"
        elif Qt.Key_F1 <= key <= Qt.Key_F12:
            key_name = f"F{key - Qt.Key_F1 + 1}"
        elif Qt.Key_A <= key <= Qt.Key_Z:
            key_name = chr(key)

        if key_name is None:
            super().keyPressEvent(event)
            return

        if not modifiers:
            modifiers = ["Ctrl", "Alt"]
        self.modifiers = modifiers
        self.key_name = key_name
        self.setText(format_hotkey(self.modifiers, self.key_name))
        self.hotkey_changed.emit(self.modifiers, self.key_name)
        event.accept()


def create_app_icon() -> QIcon:
    pixmap = QPixmap(64, 64)
    pixmap.fill(Qt.transparent)

    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setPen(Qt.NoPen)
    painter.setBrush(QColor("#0f172a"))
    painter.drawRoundedRect(4, 4, 56, 56, 18, 18)
    painter.setBrush(QColor("#f59e0b"))
    painter.drawRoundedRect(10, 10, 10, 44, 5, 5)
    painter.setBrush(QColor(255, 255, 255, 220))
    painter.drawRoundedRect(24, 16, 26, 8, 4, 4)
    painter.drawRoundedRect(24, 30, 18, 8, 4, 4)
    painter.drawRoundedRect(24, 44, 22, 8, 4, 4)
    painter.end()
    return QIcon(pixmap)


def create_folder_icon(size: int = 72) -> QIcon:
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.transparent)

    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setPen(Qt.NoPen)

    painter.setBrush(QColor("#f6c453"))
    painter.drawRoundedRect(int(size * 0.12), int(size * 0.28), int(size * 0.76), int(size * 0.48), 10, 10)
    painter.setBrush(QColor("#ffd978"))
    painter.drawRoundedRect(int(size * 0.18), int(size * 0.18), int(size * 0.28), int(size * 0.16), 8, 8)
    painter.drawRoundedRect(int(size * 0.12), int(size * 0.24), int(size * 0.76), int(size * 0.18), 10, 10)
    painter.setBrush(QColor(255, 255, 255, 55))
    painter.drawRoundedRect(int(size * 0.18), int(size * 0.34), int(size * 0.50), int(size * 0.08), 4, 4)
    painter.end()
    return QIcon(pixmap)


def create_file_icon(size: int = 72) -> QIcon:
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.transparent)

    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setPen(Qt.NoPen)
    painter.setBrush(QColor("#dbeafe"))
    painter.drawRoundedRect(int(size * 0.20), int(size * 0.12), int(size * 0.54), int(size * 0.72), 10, 10)
    painter.setBrush(QColor("#bfdbfe"))
    fold = [
        QPoint(int(size * 0.58), int(size * 0.12)),
        QPoint(int(size * 0.74), int(size * 0.12)),
        QPoint(int(size * 0.74), int(size * 0.28)),
    ]
    painter.drawPolygon(fold)
    painter.setBrush(QColor("#60a5fa"))
    painter.drawRoundedRect(int(size * 0.28), int(size * 0.34), int(size * 0.38), int(size * 0.06), 4, 4)
    painter.drawRoundedRect(int(size * 0.28), int(size * 0.46), int(size * 0.30), int(size * 0.06), 4, 4)
    painter.drawRoundedRect(int(size * 0.28), int(size * 0.58), int(size * 0.34), int(size * 0.06), 4, 4)
    painter.end()
    return QIcon(pixmap)


class ShortcutChip(QToolButton):
    def __init__(self, item: ShortcutItem, parent: QWidget | None = None):
        super().__init__(parent)
        self.item = item
        self.setText(item.name)
        self.setToolButtonStyle(Qt.ToolButtonTextUnderIcon)
        self.setIcon(create_folder_icon() if item.kind == "folder" else create_file_icon())
        self.setIconSize(QSize(56, 56))
        self.setCursor(Qt.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setMinimumHeight(118)
        self.setToolTip(self.item.path)
        self.clicked.connect(lambda: open_path(self.item.path))


class SettingsList(QListWidget):
    items_changed = Signal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setDragDropMode(QAbstractItemView.DragDrop)
        self.setDefaultDropAction(Qt.MoveAction)
        self.setSelectionMode(QAbstractItemView.SingleSelection)
        self.setAlternatingRowColors(True)
        self.setStyleSheet(
            """
            QListWidget {
                background: rgba(247, 248, 251, 0.98);
                border: 1px solid rgba(148, 163, 184, 0.4);
                border-radius: 16px;
                padding: 8px;
                font-size: 14px;
            }
            QListWidget::item {
                border-radius: 10px;
                padding: 8px 10px;
            }
            QListWidget::item:selected {
                background: rgba(15, 23, 42, 0.9);
                color: white;
            }
            """
        )
        self.model().rowsMoved.connect(lambda *args: self.items_changed.emit())

    def dragEnterEvent(self, event: QDragEnterEvent):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
            return
        super().dragEnterEvent(event)

    def dragMoveEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
            return
        super().dragMoveEvent(event)

    def dropEvent(self, event: QDropEvent):
        if event.mimeData().hasUrls():
            insert_row = self.indexAt(event.position().toPoint()).row()
            if insert_row < 0:
                insert_row = self.count()
            for url in event.mimeData().urls():
                path = url.toLocalFile()
                if path:
                    self.insert_shortcut(
                        ShortcutItem(name=display_name(path), path=path, kind=item_kind(path)),
                        insert_row,
                    )
                    insert_row += 1
            self.items_changed.emit()
            event.acceptProposedAction()
            return
        super().dropEvent(event)
        self.items_changed.emit()

    def insert_shortcut(self, item: ShortcutItem, row: int | None = None):
        list_item = QListWidgetItem(f"{item.name}  [{item.kind}]")
        list_item.setToolTip(item.path)
        list_item.setData(Qt.UserRole, item)
        if row is None:
            self.addItem(list_item)
        else:
            self.insertItem(row, list_item)

    def shortcuts(self) -> list[ShortcutItem]:
        return [
            self.item(index).data(Qt.UserRole)
            for index in range(self.count())
            if isinstance(self.item(index).data(Qt.UserRole), ShortcutItem)
        ]


class SettingsDialog(QDialog):
    def __init__(self, config: AppConfig, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("EdgeDockTool 设置")
        self.resize(760, 560)
        self.config = AppConfig(
            edge=config.edge,
            offset=config.offset,
            hover_delay_ms=config.hover_delay_ms,
            hide_delay_ms=config.hide_delay_ms,
            pinned_position=config.pinned_position,
            launch_mode=config.launch_mode,
            hotkey_modifiers=list(config.hotkey_modifiers),
            hotkey_key=config.hotkey_key,
            shortcuts=list(config.shortcuts),
        )
        self.setStyleSheet(
            """
            QDialog {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                    stop:0 rgba(255, 251, 235, 1.0),
                    stop:1 rgba(226, 232, 240, 1.0));
            }
            QLabel#title {
                font-size: 26px;
                font-weight: 700;
                color: #111827;
            }
            QLabel#subtitle {
                color: #475569;
                font-size: 14px;
            }
            QPushButton {
                background: #111827;
                color: white;
                border: none;
                border-radius: 12px;
                padding: 10px 16px;
                font-size: 14px;
            }
            QPushButton:hover {
                background: #1f2937;
            }
            QComboBox {
                background: rgba(255,255,255,0.88);
                border: 1px solid rgba(148, 163, 184, 0.45);
                border-radius: 12px;
                padding: 10px 12px;
                min-width: 160px;
                font-size: 14px;
            }
            """
        )

        title = QLabel("快捷入口设置", self)
        title.setObjectName("title")
        subtitle = QLabel("拖拽文件夹或文件进列表，双屏下会跟随鼠标所在屏幕边缘展开。", self)
        subtitle.setObjectName("subtitle")

        self.edge_combo = QComboBox(self)
        for key, label in EDGE_LABELS.items():
            self.edge_combo.addItem(label, key)
        self.edge_combo.setCurrentIndex(max(0, self.edge_combo.findData(self.config.edge)))
        self.edge_combo.currentIndexChanged.connect(self.on_changed)

        self.mode_combo = QComboBox(self)
        for key, label in MODE_LABELS.items():
            self.mode_combo.addItem(label, key)
        self.mode_combo.setCurrentIndex(max(0, self.mode_combo.findData(self.config.launch_mode)))
        self.mode_combo.currentIndexChanged.connect(self.on_changed)

        self.hotkey_input = HotkeyInput(self.config.hotkey_modifiers, self.config.hotkey_key, self)
        self.hotkey_input.hotkey_changed.connect(self.on_hotkey_changed)

        self.hover_delay_input = QLineEdit(self)
        self.hover_delay_input.setText(str(self.config.hover_delay_ms / 1000))
        self.hover_delay_input.setPlaceholderText("例如 1 或 0.5")
        self.hover_delay_input.textChanged.connect(self.on_changed)

        self.hide_delay_input = QLineEdit(self)
        self.hide_delay_input.setText(str(self.config.hide_delay_ms / 1000))
        self.hide_delay_input.setPlaceholderText("例如 0.24 或 1")
        self.hide_delay_input.textChanged.connect(self.on_changed)

        self.pinned_checkbox = QCheckBox("固定位置", self)
        self.pinned_checkbox.setChecked(self.config.pinned_position)
        self.pinned_checkbox.stateChanged.connect(self.on_changed)

        self.list = SettingsList(self)
        for item in self.config.shortcuts:
            self.list.insert_shortcut(item)
        self.list.items_changed.connect(self.on_changed)

        add_file_btn = QPushButton("添加文件", self)
        add_file_btn.clicked.connect(self.add_files)
        add_folder_btn = QPushButton("添加文件夹", self)
        add_folder_btn.clicked.connect(self.add_folder)
        remove_btn = QPushButton("删除选中", self)
        remove_btn.clicked.connect(self.remove_selected)
        save_btn = QPushButton("保存并关闭", self)
        save_btn.clicked.connect(self.accept)

        header_row = QHBoxLayout()
        header_row.addWidget(title)
        header_row.addStretch(1)
        header_row.addWidget(QLabel("打开方式", self))
        header_row.addWidget(self.mode_combo)
        header_row.addWidget(QLabel("停靠位置", self))
        header_row.addWidget(self.edge_combo)

        delay_row = QHBoxLayout()
        delay_row.addWidget(QLabel("悬停展开秒数", self))
        delay_row.addWidget(self.hover_delay_input)
        delay_row.addWidget(QLabel("离开隐藏秒数", self))
        delay_row.addWidget(self.hide_delay_input)
        delay_row.addWidget(self.pinned_checkbox)
        delay_row.addStretch(1)

        hotkey_row = QHBoxLayout()
        hotkey_row.addWidget(QLabel("快捷键", self))
        hotkey_row.addWidget(self.hotkey_input)
        hotkey_row.addStretch(1)

        buttons = QHBoxLayout()
        buttons.addWidget(add_file_btn)
        buttons.addWidget(add_folder_btn)
        buttons.addWidget(remove_btn)
        buttons.addStretch(1)
        buttons.addWidget(save_btn)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(26, 24, 26, 24)
        layout.setSpacing(16)
        layout.addLayout(header_row)
        layout.addWidget(subtitle)
        layout.addLayout(delay_row)
        layout.addLayout(hotkey_row)
        layout.addWidget(self.list)
        layout.addLayout(buttons)

    def add_files(self):
        paths, _ = QFileDialog.getOpenFileNames(self, "选择文件")
        for path in paths:
            self.list.insert_shortcut(ShortcutItem(name=display_name(path), path=path, kind=item_kind(path)))
        self.on_changed()

    def add_folder(self):
        path = QFileDialog.getExistingDirectory(self, "选择文件夹")
        if path:
            self.list.insert_shortcut(ShortcutItem(name=display_name(path), path=path, kind=item_kind(path)))
            self.on_changed()

    def remove_selected(self):
        for item in self.list.selectedItems():
            self.list.takeItem(self.list.row(item))
        self.on_changed()

    def on_changed(self):
        self.config.launch_mode = self.mode_combo.currentData() or "hover"
        self.config.edge = self.edge_combo.currentData() or "right"
        try:
            seconds = float(self.hover_delay_input.text().strip() or "1")
        except ValueError:
            seconds = 1.0
        self.config.hover_delay_ms = max(100, min(5000, int(seconds * 1000)))
        try:
            hide_seconds = float(self.hide_delay_input.text().strip() or "0.24")
        except ValueError:
            hide_seconds = 0.24
        self.config.hide_delay_ms = max(0, min(5000, int(hide_seconds * 1000)))
        self.config.pinned_position = self.pinned_checkbox.isChecked()
        self.config.shortcuts = self.list.shortcuts()

    def on_hotkey_changed(self, modifiers: list[str], key: str):
        self.config.hotkey_modifiers = list(modifiers)
        self.config.hotkey_key = key


class EdgeDockWindow(QWidget):
    collapsed_thickness = 28
    collapsed_length = 120
    expanded_width = 340
    expanded_height = 520
    hover_margin = 24

    def __init__(self, config: AppConfig):
        super().__init__()
        self.config = config
        self.edge = config.edge
        self.offset = config.offset
        self.hover_delay_ms = config.hover_delay_ms
        self.hide_delay_ms = config.hide_delay_ms
        self.pinned_position = config.pinned_position
        self.launch_mode = config.launch_mode
        self.hotkey_modifiers = list(config.hotkey_modifiers)
        self.hotkey_key = config.hotkey_key
        self.expanded = False
        self.current_screen_geo = QGuiApplication.primaryScreen().availableGeometry()
        self.anchor_screen_geo = self.current_screen_geo
        self.drag_origin_global: QPoint | None = None
        self.drag_origin_rect: QRect | None = None
        self.drag_started = False
        self.drag_hold_timer = QTimer(self)
        self.drag_hold_timer.setSingleShot(True)
        self.drag_hold_timer.timeout.connect(self.begin_drag_mode)
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Tool | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setMouseTracking(True)

        self.hover_delay = QTimer(self)
        self.hover_delay.setSingleShot(True)
        self.hover_delay.timeout.connect(self.expand)

        self.collapse_delay = QTimer(self)
        self.collapse_delay.setSingleShot(True)
        self.collapse_delay.timeout.connect(self.collapse)

        self.poll_timer = QTimer(self)
        self.poll_timer.timeout.connect(self.follow_cursor)
        self.poll_timer.start(120)

        self.card = QFrame(self)
        self.card.setObjectName("card")
        self.card.setStyleSheet(
            """
            QFrame#card {
                background: rgba(15, 23, 42, 0.94);
                border: 1px solid rgba(255,255,255,0.22);
                border-radius: 22px;
            }
            QLabel#header {
                color: white;
                font-size: 20px;
                font-weight: 700;
            }
            QLabel#subtitle {
                color: rgba(226, 232, 240, 0.78);
                font-size: 13px;
            }
            QToolButton {
                color: white;
                background: rgba(255,255,255,0.1);
                border: 1px solid rgba(255,255,255,0.05);
                border-radius: 16px;
                padding: 12px 14px;
                text-align: left;
                font-size: 14px;
            }
            QToolButton:hover {
                background: rgba(245, 158, 11, 0.24);
                border: 1px solid rgba(245, 158, 11, 0.45);
            }
            QScrollArea {
                background: transparent;
                border: none;
            }
            QScrollArea > QWidget > QWidget {
                background: transparent;
            }
            """
        )
        shadow = QGraphicsDropShadowEffect(self.card)
        shadow.setBlurRadius(34)
        shadow.setOffset(0, 12)
        shadow.setColor(QColor(15, 23, 42, 120))
        self.card.setGraphicsEffect(shadow)

        self.collapsed_hint = QLabel("EDGE", self.card)
        self.collapsed_hint.setStyleSheet("color: rgba(255,255,255,0.85); font-size: 11px; font-weight: 700; letter-spacing: 1px;")
        self.collapsed_hint.setAlignment(Qt.AlignCenter)

        self.scroll = QScrollArea(self.card)
        self.scroll.setWidgetResizable(True)
        self.scroll.setStyleSheet("background: transparent; border: none;")
        self.scroll_widget = QWidget()
        self.scroll_widget.setStyleSheet("background: transparent;")
        self.scroll_layout = QGridLayout(self.scroll_widget)
        self.scroll_layout.setContentsMargins(0, 0, 0, 0)
        self.scroll_layout.setHorizontalSpacing(12)
        self.scroll_layout.setVerticalSpacing(14)
        self.scroll_layout.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        self.scroll.setWidget(self.scroll_widget)

        layout = QVBoxLayout(self.card)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(12)
        layout.addWidget(self.collapsed_hint)
        layout.addWidget(self.scroll)

        self.refresh_shortcuts()
        self.setGeometry(self.target_geometry(expanded=False))
        self.card.setGeometry(self.rect())
        self.show()

    def refresh_shortcuts(self):
        while self.scroll_layout.count():
            item = self.scroll_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        if not self.config.shortcuts:
            empty = QLabel("右键托盘图标打开设置，然后拖进常用文件和文件夹。", self.scroll_widget)
            empty.setWordWrap(True)
            empty.setStyleSheet("color: rgba(226, 232, 240, 0.75); font-size: 13px;")
            self.scroll_layout.addWidget(empty, 0, 0, 1, 2)
        else:
            for index, item in enumerate(self.config.shortcuts):
                row = index // 2
                column = index % 2
                self.scroll_layout.addWidget(ShortcutChip(item, self.scroll_widget), row, column)
            self.scroll_layout.setColumnStretch(0, 1)
            self.scroll_layout.setColumnStretch(1, 1)
            self.scroll_layout.setRowStretch((len(self.config.shortcuts) + 1) // 2, 1)
        self.update_collapsed_presentation()

    def screen_for_cursor(self):
        return QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()

    def update_screen_context(self):
        self.current_screen_geo = self.screen_for_cursor().availableGeometry()

    def active_screen_geometry(self) -> QRect:
        if self.expanded:
            return self.anchor_screen_geo
        return self.current_screen_geo

    def target_geometry(self, expanded: bool) -> QRect:
        self.update_screen_context()
        if expanded:
            if self.launch_mode != "hotkey":
                self.anchor_screen_geo = self.current_screen_geo
            geo = self.anchor_screen_geo
        else:
            geo = self.anchor_screen_geo if self.expanded else self.active_screen_geometry()

        if self.edge in {"left", "right"}:
            width = self.expanded_width if expanded else self.collapsed_thickness
            height = min(self.expanded_height, max(180, geo.height() - 120)) if expanded else self.collapsed_length
            min_y = geo.top() + 20
            max_y = geo.bottom() - height - 20
            centered_y = geo.top() + max(30, (geo.height() - height) // 2)
            y = max(min_y, min(max_y, centered_y + self.offset))
            if self.edge == "left":
                x = geo.left()
            else:
                x = geo.right() - width + 1
            return QRect(x, y, width, height)

        height = self.expanded_height if expanded else self.collapsed_thickness
        width = min(self.expanded_width, max(220, geo.width() - 160)) if expanded else 140
        min_x = geo.left() + 20
        max_x = geo.right() - width - 20
        centered_x = geo.left() + max(40, (geo.width() - width) // 2)
        x = max(min_x, min(max_x, centered_x + self.offset))
        if self.edge == "top":
            y = geo.top()
        else:
            y = geo.bottom() - height + 1
        return QRect(x, y, width, height)

    def update_edge(self, edge: str):
        self.edge = edge
        self.config.edge = edge
        self.config.offset = self.offset
        self.setGeometry(self.target_geometry(self.expanded))
        self.card.setGeometry(self.rect())
        self.update_collapsed_presentation()

    def update_hover_delay(self, hover_delay_ms: int):
        self.hover_delay_ms = hover_delay_ms
        self.config.hover_delay_ms = hover_delay_ms

    def update_hide_delay(self, hide_delay_ms: int):
        self.hide_delay_ms = hide_delay_ms
        self.config.hide_delay_ms = hide_delay_ms

    def update_pinned_position(self, pinned_position: bool):
        self.pinned_position = pinned_position
        self.config.pinned_position = pinned_position

    def update_launch_mode(self, launch_mode: str):
        self.launch_mode = launch_mode
        self.config.launch_mode = launch_mode
        if self.launch_mode == "hotkey":
            self.hover_delay.stop()
            self.collapse_delay.stop()
            if not self.expanded:
                self.hide()
        else:
            self.show()
            self.raise_()
            self.setGeometry(self.target_geometry(self.expanded))
            self.card.setGeometry(self.rect())

    def update_hotkey(self, modifiers: list[str], key: str):
        self.hotkey_modifiers = list(modifiers)
        self.hotkey_key = key
        self.config.hotkey_modifiers = list(modifiers)
        self.config.hotkey_key = key

    def cursor_near_active_edge(self, pos: QPoint) -> bool:
        if self.geometry().adjusted(-10, -10, 10, 10).contains(pos):
            return True
        geo = self.screen_for_cursor().availableGeometry()
        if self.edge == "left":
            return geo.left() <= pos.x() <= geo.left() + self.hover_margin and self.geometry().top() - 30 <= pos.y() <= self.geometry().bottom() + 30
        if self.edge == "right":
            return geo.right() - self.hover_margin <= pos.x() <= geo.right() and self.geometry().top() - 30 <= pos.y() <= self.geometry().bottom() + 30
        if self.edge == "top":
            return geo.top() <= pos.y() <= geo.top() + self.hover_margin and self.geometry().left() - 30 <= pos.x() <= self.geometry().right() + 30
        return geo.bottom() - self.hover_margin <= pos.y() <= geo.bottom() and self.geometry().left() - 30 <= pos.x() <= self.geometry().right() + 30

    def follow_cursor(self):
        if self.launch_mode == "hotkey":
            return
        pos = QCursor.pos()
        if not self.expanded and self.cursor_near_active_edge(pos):
            if not self.hover_delay.isActive():
                self.hover_delay.start(self.hover_delay_ms)
                self.anchor_screen_geo = self.screen_for_cursor().availableGeometry()
            return

        if not self.expanded:
            self.hover_delay.stop()
            return

        padded = self.geometry().adjusted(-12, -12, 12, 12)
        if padded.contains(pos):
            self.collapse_delay.stop()
        elif not self.collapse_delay.isActive():
            self.collapse_delay.start(self.hide_delay_ms)

    def expand(self):
        if self.expanded:
            return
        self.expanded = True
        self.show()
        self.update_collapsed_presentation()
        self.animate_to(self.target_geometry(expanded=True))

    def collapse(self):
        if not self.expanded:
            return
        target = self.target_geometry(expanded=False)
        self.expanded = False
        self.update_collapsed_presentation()
        self.animate_to(target)
        if self.launch_mode == "hotkey":
            self.hide()

    def animate_to(self, geometry: QRect):
        self.show()
        self.raise_()
        self.setWindowOpacity(1.0)
        self.setGeometry(geometry)
        self.card.setGeometry(self.rect())

    def resizeEvent(self, event):
        self.card.setGeometry(self.rect())
        if not self.expanded:
            self.collapsed_hint.setGeometry(self.card.rect())
        super().resizeEvent(event)

    def update_collapsed_presentation(self):
        collapsed = not self.expanded
        self.scroll.setVisible(not collapsed)
        self.collapsed_hint.setVisible(collapsed)
        if collapsed:
            self.card.layout().setContentsMargins(0, 0, 0, 0)
            self.card.layout().setSpacing(0)
            self.collapsed_hint.setGeometry(self.card.rect())
            if self.edge in {"left", "right"}:
                self.collapsed_hint.setText("EDGE")
            else:
                self.collapsed_hint.setText("EDGE")
        else:
            self.card.layout().setContentsMargins(20, 18, 20, 18)
            self.card.layout().setSpacing(12)

    def begin_drag_mode(self):
        self.drag_started = True
        self.hover_delay.stop()
        self.collapse_delay.stop()

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            if self.launch_mode == "hotkey" and not self.pinned_position:
                if self.geometry().adjusted(-10, -10, 10, 10).contains(event.globalPosition().toPoint()):
                    if self.expanded:
                        self.collapse()
                    else:
                        self.expand()
                    super().mousePressEvent(event)
                    return
            if self.pinned_position:
                super().mousePressEvent(event)
                return
            self.drag_origin_global = event.globalPosition().toPoint()
            self.drag_origin_rect = QRect(self.geometry())
            self.drag_started = False
            self.drag_hold_timer.start(420)
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self.drag_origin_global is None or self.drag_origin_rect is None:
            super().mouseMoveEvent(event)
            return

        current = event.globalPosition().toPoint()
        delta = current - self.drag_origin_global
        if not self.drag_started and delta.manhattanLength() > 8:
            self.drag_hold_timer.stop()

        if not self.drag_started:
            super().mouseMoveEvent(event)
            return

        geo = self.screen_for_cursor().availableGeometry()
        self.anchor_screen_geo = geo
        margin = 72
        if abs(current.x() - geo.left()) <= margin:
            self.edge = "left"
            self.offset = current.y() - (geo.top() + geo.height() // 2)
        elif abs(current.x() - geo.right()) <= margin:
            self.edge = "right"
            self.offset = current.y() - (geo.top() + geo.height() // 2)
        elif abs(current.y() - geo.top()) <= margin:
            self.edge = "top"
            self.offset = current.x() - (geo.left() + geo.width() // 2)
        elif abs(current.y() - geo.bottom()) <= margin:
            self.edge = "bottom"
            self.offset = current.x() - (geo.left() + geo.width() // 2)
        self.config.edge = self.edge
        self.config.offset = self.offset
        self.setGeometry(self.target_geometry(self.expanded))
        self.card.setGeometry(self.rect())
        self.update_collapsed_presentation()
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self.drag_hold_timer.stop()
        if self.drag_started:
            save_config(self.config)
        self.drag_origin_global = None
        self.drag_origin_rect = None
        self.drag_started = False
        super().mouseReleaseEvent(event)


class AppTray(QApplication):
    def __init__(self, argv: list[str]):
        super().__init__(argv)
        self.setQuitOnLastWindowClosed(False)
        self.icon = create_app_icon()
        self.config = load_config()
        self.window = EdgeDockWindow(self.config)
        self.settings_dialog: SettingsDialog | None = None
        self.hotkey_filter = HotkeyFilter(self.toggle_window_from_hotkey)
        self.installNativeEventFilter(self.hotkey_filter)
        self.hotkey_registered = False

        self.tray = QSystemTrayIcon(self.icon, self)
        self.update_tray_tooltip()
        self.tray.setContextMenu(self.build_menu())
        self.tray.activated.connect(self.on_tray_activated)
        self.tray.show()

        self.sync_hotkey_registration()
        if not self.config.shortcuts:
            QTimer.singleShot(250, self.open_settings)

    def build_menu(self) -> QMenu:
        menu = QMenu()
        settings_action = QAction("设置", self)
        settings_action.triggered.connect(self.open_settings)
        restart_action = QAction("重启", self)
        restart_action.triggered.connect(self.restart_app)
        quit_action = QAction("退出", self)
        quit_action.triggered.connect(self.quit)
        menu.addAction(settings_action)
        menu.addAction(restart_action)
        menu.addSeparator()
        menu.addAction(quit_action)
        return menu

    def open_settings(self):
        if self.settings_dialog and self.settings_dialog.isVisible():
            self.settings_dialog.raise_()
            self.settings_dialog.activateWindow()
            return
        self.settings_dialog = SettingsDialog(self.config)
        if self.settings_dialog.exec():
            self.config = self.settings_dialog.config
            save_config(self.config)
            self.window.config = self.config
            self.window.refresh_shortcuts()
            self.window.update_edge(self.config.edge)
            self.window.update_hover_delay(self.config.hover_delay_ms)
            self.window.update_hide_delay(self.config.hide_delay_ms)
            self.window.update_pinned_position(self.config.pinned_position)
            self.window.update_launch_mode(self.config.launch_mode)
            self.window.update_hotkey(self.config.hotkey_modifiers, self.config.hotkey_key)
            self.update_tray_tooltip()
            self.sync_hotkey_registration()
        self.settings_dialog = None

    def on_tray_activated(self, reason):
        if reason == QSystemTrayIcon.Trigger:
            self.open_settings()

    def restart_app(self):
        save_config(self.config)
        QApplication.exit(42)

    def sync_hotkey_registration(self):
        self.unregister_hotkey()
        if self.config.launch_mode == "hotkey":
            self.register_hotkey()

    def register_hotkey(self):
        if self.hotkey_registered:
            return
        modifier_mask = 0
        for item in self.config.hotkey_modifiers:
            modifier_mask |= MODIFIER_FLAGS.get(item, 0)
        vk = VIRTUAL_KEYS.get(self.config.hotkey_key, VIRTUAL_KEYS["Space"])
        if user32.RegisterHotKey(None, HOTKEY_ID, modifier_mask, vk):
            self.hotkey_registered = True

    def unregister_hotkey(self):
        if not self.hotkey_registered:
            return
        user32.UnregisterHotKey(None, HOTKEY_ID)
        self.hotkey_registered = False

    def toggle_window_from_hotkey(self):
        if self.window.expanded:
            self.window.collapse()
        else:
            self.window.expand()

    def update_tray_tooltip(self):
        hotkey_text = format_hotkey(self.config.hotkey_modifiers, self.config.hotkey_key)
        self.tray.setToolTip(f"EdgeDockTool\n快捷键模式: {hotkey_text}")


def run():
    app = AppTray(sys.argv)
    code = app.exec()
    if code == 42:
        Popen([sys.executable, str(Path(__file__).resolve().parent.parent / "main.py")])
