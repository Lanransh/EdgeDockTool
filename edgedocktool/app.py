from __future__ import annotations

import os
import sys
import time
import traceback
from datetime import datetime
from ctypes import Structure, WinDLL, byref, c_int, c_size_t, c_void_p, cast, sizeof, wintypes
from pathlib import Path
from subprocess import CREATE_NEW_PROCESS_GROUP, DETACHED_PROCESS, Popen, list2cmdline

try:
    import winreg
except ImportError:  # pragma: no cover - only used on Windows
    winreg = None

from PySide6.QtCore import (
    QAbstractNativeEventFilter,
    QEasingCurve,
    QEvent,
    QFileInfo,
    QPoint,
    QPropertyAnimation,
    QRect,
    QSize,
    Qt,
    QTimer,
    QSharedMemory,
    Signal,
)
from PySide6.QtGui import (
    QAction,
    QColor,
    QDragEnterEvent,
    QDropEvent,
    QIcon,
    QKeyEvent,
    QPainter,
    QPixmap,
)
from PySide6.QtWidgets import (
    QApplication,
    QAbstractItemView,
    QCheckBox,
    QDialog,
    QFileDialog,
    QFileIconProvider,
    QFrame,
    QGraphicsOpacityEffect,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSystemTrayIcon,
    QToolButton,
    QVBoxLayout,
    QWidget,
)
from PySide6.QtNetwork import QLocalServer, QLocalSocket

from .config import AppConfig, ShortcutItem, load_config, save_config
from .utils import display_name, item_kind, open_path

APP_NAME = "EdgeDockTool"
APP_VERSION = "0.2.0"
RESTART_EXIT_CODE = 42
INSTANCE_SERVER_NAME = "EdgeDockTool.SingleInstance.v1"
HOTKEY_ID = 1
WM_HOTKEY = 0x0312
MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004

MODIFIER_FLAGS = {"Ctrl": MOD_CONTROL, "Alt": MOD_ALT, "Shift": MOD_SHIFT}
MODIFIER_VKEYS = {"Ctrl": 0x11, "Alt": 0x12, "Shift": 0x10}
VIRTUAL_KEYS = {"Space": 0x20, **{f"F{i}": 0x6F + i for i in range(1, 13)}}
VIRTUAL_KEYS.update({chr(code): code for code in range(ord("A"), ord("Z") + 1)})

RESIZE_BORDER = 8

if os.name == "nt":
    user32 = WinDLL("user32", use_last_error=True)
else:  # pragma: no cover - allows importing the module for tooling on other platforms
    user32 = None


def append_diagnostic(message: str) -> None:
    try:
        base = Path(os.environ.get("APPDATA") or Path.home()) / APP_NAME
        base.mkdir(parents=True, exist_ok=True)
        with (base / "error.log").open("a", encoding="utf-8") as log:
            log.write(f"[{datetime.now().isoformat(timespec='seconds')}] {message.rstrip()}\n")
    except OSError:
        pass


def install_exception_logger() -> None:
    previous_hook = sys.excepthook

    def log_exception(exc_type, exc_value, exc_traceback):
        append_diagnostic("".join(traceback.format_exception(exc_type, exc_value, exc_traceback)))
        if sys.stderr is not None:
            previous_hook(exc_type, exc_value, exc_traceback)

    sys.excepthook = log_exception


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


class ACCENT_POLICY(Structure):
    _fields_ = [
        ("AccentState", wintypes.DWORD),
        ("AccentFlags", wintypes.DWORD),
        ("GradientColor", wintypes.DWORD),
        ("AnimationId", wintypes.DWORD),
    ]


class WINDOWCOMPOSITIONATTRIBDATA(Structure):
    _fields_ = [
        ("Attribute", wintypes.DWORD),
        ("Data", c_void_p),
        ("SizeOfData", c_size_t),
    ]


class HotkeyFilter(QAbstractNativeEventFilter):
    def __init__(self, callback):
        super().__init__()
        self.callback = callback

    def nativeEventFilter(self, event_type, message):
        if not isinstance(event_type, str):
            try:
                event_type = bytes(event_type).decode(errors="ignore")
            except (TypeError, ValueError):
                event_type = str(event_type)
        if event_type not in {"windows_generic_MSG", "windows_dispatcher_MSG"}:
            return False, 0
        msg = MSG.from_address(int(message))
        if msg.message == WM_HOTKEY and msg.wParam == HOTKEY_ID:
            self.callback()
            return True, 0
        return False, 0


def format_hotkey(modifiers: list[str], key: str) -> str:
    return "+".join([*modifiers, key])


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
        qt_modifiers = event.modifiers()
        modifiers = []
        if qt_modifiers & Qt.ControlModifier:
            modifiers.append("Ctrl")
        if qt_modifiers & Qt.AltModifier:
            modifiers.append("Alt")
        if qt_modifiers & Qt.ShiftModifier:
            modifiers.append("Shift")

        key = event.key()
        if key == Qt.Key_Space:
            key_name = "Space"
        elif Qt.Key_F1 <= key <= Qt.Key_F12:
            key_name = f"F{key - Qt.Key_F1 + 1}"
        elif Qt.Key_A <= key <= Qt.Key_Z:
            key_name = chr(key)
        else:
            super().keyPressEvent(event)
            return

        if not modifiers:
            modifiers = ["Alt"]
        self.modifiers = modifiers
        self.key_name = key_name
        self.setText(format_hotkey(modifiers, key_name))
        self.hotkey_changed.emit(list(modifiers), key_name)
        event.accept()


def create_app_icon() -> QIcon:
    pixmap = QPixmap(64, 64)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setPen(Qt.NoPen)
    painter.setBrush(QColor("#111827"))
    painter.drawRoundedRect(4, 4, 56, 56, 17, 17)
    painter.setBrush(QColor("#f59e0b"))
    painter.drawRoundedRect(11, 11, 9, 42, 4, 4)
    painter.setBrush(QColor(255, 255, 255, 225))
    for y, width in ((17, 28), (29, 19), (41, 24)):
        painter.drawRoundedRect(25, y, width, 7, 3, 3)
    painter.end()
    return QIcon(pixmap)


def fallback_icon(kind: str, size: int = 72) -> QIcon:
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setPen(Qt.NoPen)
    if kind == "folder":
        painter.setBrush(QColor("#f6c453"))
        painter.drawRoundedRect(int(size * 0.10), int(size * 0.28), int(size * 0.80), int(size * 0.48), 9, 9)
        painter.setBrush(QColor("#ffdc7b"))
        painter.drawRoundedRect(int(size * 0.16), int(size * 0.18), int(size * 0.30), int(size * 0.16), 7, 7)
    else:
        painter.setBrush(QColor("#dbeafe"))
        painter.drawRoundedRect(int(size * 0.20), int(size * 0.12), int(size * 0.56), int(size * 0.74), 8, 8)
        painter.setBrush(QColor("#60a5fa"))
        painter.drawRoundedRect(int(size * 0.29), int(size * 0.37), int(size * 0.37), int(size * 0.06), 3, 3)
        painter.drawRoundedRect(int(size * 0.29), int(size * 0.51), int(size * 0.29), int(size * 0.06), 3, 3)
    painter.end()
    return QIcon(pixmap)


def icon_for_item(item: ShortcutItem) -> QIcon:
    icon = QFileIconProvider().icon(QFileInfo(item.path))
    return icon if not icon.isNull() else fallback_icon(item.kind)


class SettingsList(QListWidget):
    items_changed = Signal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setDragDropMode(QAbstractItemView.DragDrop)
        self.setDefaultDropAction(Qt.MoveAction)
        self.setSelectionMode(QAbstractItemView.SingleSelection)
        self.setSpacing(5)
        self.setStyleSheet(
            """
            QListWidget { background: white; border: 1px solid #dbe3ee; border-radius: 12px; padding: 7px; outline: none; }
            QListWidget::item { border: 1px solid transparent; border-radius: 9px; }
            QListWidget::item:hover { background: #f8fafc; border-color: #e2e8f0; }
            QListWidget::item:selected { background: #e8f0ff; border-color: #bfd2ff; }
            """
        )
        self.model().rowsMoved.connect(lambda *_: self.items_changed.emit())

    def dragEnterEvent(self, event: QDragEnterEvent):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dragMoveEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            super().dragMoveEvent(event)

    def dropEvent(self, event: QDropEvent):
        if not event.mimeData().hasUrls():
            super().dropEvent(event)
            self.items_changed.emit()
            return
        row = self.indexAt(event.position().toPoint()).row()
        if row < 0:
            row = self.count()
        for url in event.mimeData().urls():
            path = url.toLocalFile()
            if path:
                self.insert_shortcut(ShortcutItem(display_name(path), path, item_kind(path)), row)
                row += 1
        self.items_changed.emit()
        event.acceptProposedAction()

    def insert_shortcut(self, item: ShortcutItem, row: int | None = None):
        list_item = QListWidgetItem()
        list_item.setToolTip(item.path)
        list_item.setData(Qt.UserRole, item)
        list_item.setSizeHint(QSize(0, 62))
        if row is None:
            self.addItem(list_item)
        else:
            self.insertItem(row, list_item)
        self.setItemWidget(list_item, SettingsShortcutRow(item, self))

    def shortcuts(self) -> list[ShortcutItem]:
        return [
            item.data(Qt.UserRole)
            for index in range(self.count())
            if isinstance((item := self.item(index)).data(Qt.UserRole), ShortcutItem)
        ]


class SettingsShortcutRow(QWidget):
    def __init__(self, item: ShortcutItem, parent: QWidget | None = None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)

        icon = QLabel(self)
        icon.setPixmap(icon_for_item(item).pixmap(36, 36))
        icon.setFixedSize(42, 42)
        icon.setAlignment(Qt.AlignCenter)

        name = QLabel(item.name, self)
        name.setStyleSheet("color: #172033; font-size: 14px; font-weight: 600;")
        path = QLabel(item.path, self)
        path.setStyleSheet("color: #778399; font-size: 11px;")
        path.setToolTip(item.path)

        text = QVBoxLayout()
        text.setContentsMargins(0, 0, 0, 0)
        text.setSpacing(2)
        text.addWidget(name)
        text.addWidget(path)

        kind = QLabel("文件夹" if item.kind == "folder" else "软件 / 文件", self)
        kind.setStyleSheet("color: #52647d; background: #edf2f8; border-radius: 10px; padding: 4px 9px;")

        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 7, 10, 7)
        layout.setSpacing(10)
        layout.addWidget(icon)
        layout.addLayout(text, 1)
        layout.addWidget(kind)


class SettingsDialog(QDialog):
    def __init__(self, config: AppConfig, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle(f"{APP_NAME} 设置")
        self.resize(720, 560)
        self.config = AppConfig(
            auto_start=config.auto_start,
            hotkey_modifiers=list(config.hotkey_modifiers),
            hotkey_key=config.hotkey_key,
            shortcuts=list(config.shortcuts),
        )
        self.setStyleSheet(
            """
            QDialog { background: #f5f7fb; }
            QLabel#title { font-size: 24px; font-weight: 700; color: #172033; }
            QLabel#subtitle { color: #66758d; font-size: 12px; }
            QLabel#sectionTitle { color: #27364d; font-size: 14px; font-weight: 600; }
            QLabel#sectionHint { color: #7a879a; font-size: 11px; }
            QPushButton { background: white; color: #334155; border: 1px solid #d7e0eb; border-radius: 8px; padding: 9px 14px; }
            QPushButton:hover { background: #f8fafc; border-color: #aebcd0; }
            QPushButton#primary { background: #2563eb; color: white; border-color: #2563eb; font-weight: 600; }
            QPushButton#primary:hover { background: #1d4ed8; }
            QPushButton#danger { color: #b42318; }
            QPushButton#danger:hover { background: #fff1f0; border-color: #f0b8b3; }
            QLineEdit { background: white; border: 1px solid #d7e0eb; border-radius: 8px; padding: 8px 10px; }
            QLineEdit:focus { border-color: #7aa2f7; }
            QCheckBox { color: #41516a; spacing: 7px; }
            """
        )

        title = QLabel("管理快捷入口", self)
        title.setObjectName("title")
        subtitle = QLabel("把常用软件、文件和文件夹放到启动面板中。", self)
        subtitle.setObjectName("subtitle")

        self.hotkey_input = HotkeyInput(self.config.hotkey_modifiers, self.config.hotkey_key, self)
        self.hotkey_input.hotkey_changed.connect(self.on_hotkey_changed)
        self.auto_start_checkbox = QCheckBox("开机自启", self)
        self.auto_start_checkbox.setChecked(self.config.auto_start)
        self.auto_start_checkbox.stateChanged.connect(self.on_changed)

        self.list = SettingsList(self)
        for item in self.config.shortcuts:
            self.list.insert_shortcut(item)
        self.list.items_changed.connect(self.on_changed)

        add_file = QPushButton("添加文件")
        add_file.clicked.connect(self.add_files)
        add_folder = QPushButton("添加文件夹")
        add_folder.clicked.connect(self.add_folder)
        remove = QPushButton("移除选中")
        remove.setObjectName("danger")
        remove.clicked.connect(self.remove_selected)
        save = QPushButton("保存并关闭")
        save.setObjectName("primary")
        save.clicked.connect(self.accept)

        section_title = QLabel("已添加的项目", self)
        section_title.setObjectName("sectionTitle")
        section_hint = QLabel("可直接拖入项目；拖动列表行可以调整显示顺序", self)
        section_hint.setObjectName("sectionHint")

        hotkey_row = QHBoxLayout()
        hotkey_row.addWidget(QLabel("全局快捷键"))
        hotkey_row.addWidget(self.hotkey_input)
        hotkey_row.addWidget(self.auto_start_checkbox)
        hotkey_row.addStretch(1)

        buttons = QHBoxLayout()
        buttons.addWidget(add_file)
        buttons.addWidget(add_folder)
        buttons.addWidget(remove)
        buttons.addStretch(1)
        buttons.addWidget(save)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(26, 24, 26, 24)
        layout.setSpacing(13)
        layout.addWidget(title)
        layout.addWidget(subtitle)
        layout.addLayout(hotkey_row)
        layout.addSpacing(4)
        layout.addWidget(section_title)
        layout.addWidget(section_hint)
        layout.addWidget(self.list)
        layout.addLayout(buttons)

    def add_files(self):
        paths, _ = QFileDialog.getOpenFileNames(self, "选择文件")
        for path in paths:
            self.list.insert_shortcut(ShortcutItem(display_name(path), path, item_kind(path)))
        self.on_changed()

    def add_folder(self):
        path = QFileDialog.getExistingDirectory(self, "选择文件夹")
        if path:
            self.list.insert_shortcut(ShortcutItem(display_name(path), path, item_kind(path)))
            self.on_changed()

    def remove_selected(self):
        for item in self.list.selectedItems():
            self.list.takeItem(self.list.row(item))
        self.on_changed()

    def on_changed(self):
        self.config.auto_start = self.auto_start_checkbox.isChecked()
        self.config.shortcuts = self.list.shortcuts()

    def on_hotkey_changed(self, modifiers: list[str], key: str):
        self.config.hotkey_modifiers = list(modifiers)
        self.config.hotkey_key = key


class ShortcutButton(QToolButton):
    def __init__(self, item: ShortcutItem, parent: QWidget | None = None):
        super().__init__(parent)
        self.item = item
        self.setText(item.name)
        self.setToolButtonStyle(Qt.ToolButtonTextUnderIcon)
        self.setIcon(icon_for_item(item))
        self.setIconSize(QSize(64, 64))
        self.setMinimumSize(112, 112)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setToolTip(item.path)
        self.setStyleSheet(
            """
            QToolButton { color: #edf2f7; background: rgba(255,255,255,0.045); border: 1px solid rgba(255,255,255,0.07); border-radius: 13px; padding: 10px 6px; font-size: 13px; }
            QToolButton:hover, QToolButton:focus { background: rgba(255,255,255,0.12); border-color: rgba(147,197,253,0.72); }
            QToolButton:pressed { background: rgba(96,165,250,0.22); }
            """
        )
        self.clicked.connect(self._animate_click)

    def _animate_click(self):
        effect = self.graphicsEffect()
        if not isinstance(effect, QGraphicsOpacityEffect):
            effect = QGraphicsOpacityEffect(self)
            self.setGraphicsEffect(effect)
        animation = QPropertyAnimation(effect, b"opacity", self)
        animation.setDuration(150)
        animation.setStartValue(1.0)
        animation.setKeyValueAt(0.45, 0.48)
        animation.setEndValue(1.0)
        animation.finished.connect(self._launch_after_animation)
        animation.start(QPropertyAnimation.DeleteWhenStopped)

    def _launch_after_animation(self):
        open_path(self.item.path)
        window = self.window()
        hide_popup = getattr(window, "hide_popup", None)
        if callable(hide_popup):
            hide_popup()

    def keyPressEvent(self, event: QKeyEvent):
        if event.key() == Qt.Key_Escape:
            hide_popup = getattr(self.window(), "hide_popup", None)
            if callable(hide_popup):
                hide_popup()
                event.accept()
                return
        super().keyPressEvent(event)


class ResizeHandle(QWidget):
    def __init__(self, edges, cursor, parent: QWidget):
        super().__init__(parent)
        self.edges = edges
        self.setCursor(cursor)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            handle = self.window().windowHandle()
            if handle is not None and handle.startSystemResize(self.edges):
                event.accept()
                return
        super().mousePressEvent(event)


class LauncherWindow(QWidget):
    settings_requested = Signal()

    def __init__(self, config: AppConfig):
        super().__init__()
        self.config = config
        self.visible_buttons: list[ShortcutButton] = []
        self.buttons: list[ShortcutButton] = []
        self.animating = False
        self._backdrop_applied = False
        self._backdrop_attempted = False
        self._popup_animation: QPropertyAnimation | None = None
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Tool | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setMinimumSize(540, 360)
        self.setWindowTitle(APP_NAME)
        self.setStyleSheet("QWidget#panel { background: rgba(20, 20, 24, 96); border: 1px solid rgba(255,255,255,0.16); border-radius: 16px; }")

        self.panel = QFrame(self)
        self.panel.setObjectName("panel")

        self.title = QLabel("快捷启动", self.panel)
        self.title.setStyleSheet("color: #f8fafc; font-size: 18px; font-weight: 650;")

        self.search = QLineEdit(self.panel)
        self.search.setPlaceholderText("搜索软件、文件或文件夹")
        self.search.setClearButtonEnabled(True)
        self.search.setMinimumWidth(240)
        self.search.setMaximumWidth(470)
        self.search.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.search.setAlignment(Qt.AlignLeft)
        self.search.setStyleSheet(
            "QLineEdit { background: rgba(7,9,13,0.66); color: #f8fafc; border: 1px solid rgba(148,163,184,0.34); border-radius: 10px; padding: 10px 13px; font-size: 13px; } QLineEdit:focus { border-color: #7aa2f7; }"
        )
        self.search.installEventFilter(self)
        self.search.textChanged.connect(self.relayout)

        self.manage_button = QPushButton("⚙  管理软件", self.panel)
        self.manage_button.setCursor(Qt.PointingHandCursor)
        self.manage_button.setToolTip("添加、移除或排序快捷入口")
        self.manage_button.setStyleSheet(
            "QPushButton { color: #e8eef8; background: rgba(255,255,255,0.08); border: 1px solid rgba(255,255,255,0.13); border-radius: 10px; padding: 10px 14px; font-size: 12px; font-weight: 600; } QPushButton:hover { background: rgba(255,255,255,0.15); border-color: rgba(147,197,253,0.62); } QPushButton:pressed { background: rgba(96,165,250,0.20); }"
        )
        self.manage_button.clicked.connect(self.open_software_manager)

        self.status = QLabel("", self.panel)
        self.status.setStyleSheet("color: rgba(226,232,240,0.62); font-size: 12px;")
        self.status.setAlignment(Qt.AlignRight | Qt.AlignVCenter)

        self.scroll = QScrollArea(self.panel)
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll.setStyleSheet("QScrollArea { background: transparent; }")
        self.content = QWidget()
        self.content.setStyleSheet("background: transparent;")
        self.grid = QGridLayout(self.content)
        self.grid.setContentsMargins(12, 8, 12, 12)
        self.grid.setHorizontalSpacing(16)
        self.grid.setVerticalSpacing(18)
        self.grid.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        self.scroll.setWidget(self.content)

        self.empty = QLabel("还没有快捷入口\n打开设置后拖入文件、文件夹或程序", self.content)
        self.empty.setAlignment(Qt.AlignCenter)
        self.empty.setStyleSheet("color: rgba(226,232,240,0.72); font-size: 14px; line-height: 1.5;")
        self.empty.setWordWrap(True)

        layout = QVBoxLayout(self.panel)
        layout.setContentsMargins(24, 20, 24, 22)
        layout.setSpacing(13)
        header = QHBoxLayout()
        header.setSpacing(14)
        header.addWidget(self.title)
        header.addStretch(1)
        header.addWidget(self.search, 1)
        header.addWidget(self.manage_button)
        layout.addLayout(header)
        layout.addWidget(self.status)
        layout.addWidget(self.scroll, 1)

        self.resize_handles = {
            "top": ResizeHandle(Qt.TopEdge, Qt.SizeVerCursor, self),
            "bottom": ResizeHandle(Qt.BottomEdge, Qt.SizeVerCursor, self),
            "left": ResizeHandle(Qt.LeftEdge, Qt.SizeHorCursor, self),
            "right": ResizeHandle(Qt.RightEdge, Qt.SizeHorCursor, self),
            "top_left": ResizeHandle(Qt.TopEdge | Qt.LeftEdge, Qt.SizeFDiagCursor, self),
            "top_right": ResizeHandle(Qt.TopEdge | Qt.RightEdge, Qt.SizeBDiagCursor, self),
            "bottom_left": ResizeHandle(Qt.BottomEdge | Qt.LeftEdge, Qt.SizeBDiagCursor, self),
            "bottom_right": ResizeHandle(Qt.BottomEdge | Qt.RightEdge, Qt.SizeFDiagCursor, self),
        }

        self.refresh_shortcuts()
        self.resize_to_screen()
        self.panel.setGeometry(self.rect().adjusted(RESIZE_BORDER, RESIZE_BORDER, -RESIZE_BORDER, -RESIZE_BORDER))
        self.position_resize_handles()
        app = QApplication.instance()
        if app is not None:
            app.installEventFilter(self)

    def resize_to_screen(self):
        screen = QApplication.screenAt(self.cursor_pos()) or QApplication.primaryScreen()
        geo = screen.availableGeometry()
        width = min(max(760, int(geo.width() * 0.94)), max(540, geo.width() - 36))
        height = min(max(470, int(geo.height() * 0.88)), max(360, geo.height() - 36))
        self.setGeometry(QRect(geo.left() + (geo.width() - width) // 2, geo.top() + (geo.height() - height) // 2, width, height))

    def center_on_current_screen(self):
        screen = QApplication.screenAt(self.cursor_pos()) or QApplication.primaryScreen()
        geo = screen.availableGeometry()
        width = min(self.width(), max(self.minimumWidth(), geo.width() - 36))
        height = min(self.height(), max(self.minimumHeight(), geo.height() - 36))
        self.setGeometry(QRect(geo.left() + (geo.width() - width) // 2, geo.top() + (geo.height() - height) // 2, width, height))

    @staticmethod
    def cursor_pos() -> QPoint:
        from PySide6.QtGui import QCursor

        return QCursor.pos()

    def refresh_shortcuts(self):
        for button in self.buttons:
            button.deleteLater()
        self.buttons = [ShortcutButton(item, self.content) for item in self.config.shortcuts]
        self.relayout()

    def relayout(self):
        while self.grid.count():
            item = self.grid.takeAt(0)
            if item.widget() is not None:
                item.widget().setParent(self.content)
        query = self.search.text().strip().casefold()
        self.visible_buttons = [button for button in self.buttons if not query or query in button.item.name.casefold() or query in button.item.path.casefold()]
        columns = max(1, min(8, (max(1, self.scroll.viewport().width()) + 16) // 132))
        for index, button in enumerate(self.visible_buttons):
            self.grid.addWidget(button, index // columns, index % columns)
            button.show()
        for button in self.buttons:
            if button not in self.visible_buttons:
                button.hide()
        self.empty.setText("没有匹配的项目" if self.buttons and query else "这里还没有快捷入口\n点击右上角“管理软件”开始添加")
        self.empty.setVisible(not self.visible_buttons)
        if self.empty.isVisible():
            self.grid.addWidget(self.empty, 0, 0, 1, max(1, columns))
        hotkey = format_hotkey(self.config.hotkey_modifiers, self.config.hotkey_key)
        self.status.setText(f"{len(self.visible_buttons)} 个应用   •   {hotkey} 呼出")

    def showEvent(self, event):
        super().showEvent(event)
        if not self._backdrop_attempted:
            self._apply_backdrop()

    def _apply_backdrop(self):
        self._backdrop_attempted = True
        handle = self.windowHandle()
        if handle is None or os.name != "nt":
            return
        hwnd = int(handle.winId())
        failures = []

        # Prefer Acrylic because it keeps the desktop visibly translucent.
        # Applying it together with the system backdrop stacks two dark tints.
        try:
            policy = ACCENT_POLICY(4, 2, 0x55141416, 0)  # ACCENT_ENABLE_ACRYLICBLURBEHIND
            data = WINDOWCOMPOSITIONATTRIBDATA(19, cast(byref(policy), c_void_p), c_size_t(sizeof(policy)))
            set_composition = user32.SetWindowCompositionAttribute
            set_composition.argtypes = [wintypes.HWND, c_void_p]
            set_composition.restype = wintypes.BOOL
            self._backdrop_applied = bool(set_composition(hwnd, byref(data)))
            if not self._backdrop_applied:
                failures.append("SetWindowCompositionAttribute returned false")
        except (OSError, AttributeError) as error:
            failures.append(f"Acrylic backdrop failed: {error}")

        if self._backdrop_applied:
            return

        # Fall back to the Windows 11 system material when Acrylic is unavailable.
        try:
            dwm = WinDLL("dwmapi", use_last_error=True)
            backdrop = c_int(3)  # DWMSBT_TRANSIENTWINDOW
            corner = c_int(2)  # DWMWCP_ROUND
            backdrop_result = dwm.DwmSetWindowAttribute(hwnd, 38, byref(backdrop), 4)
            dwm.DwmSetWindowAttribute(hwnd, 33, byref(corner), 4)
            self._backdrop_applied = backdrop_result == 0
            if backdrop_result != 0:
                failures.append(f"DwmSetWindowAttribute returned {backdrop_result}")
        except (OSError, AttributeError) as error:
            failures.append(f"DWM backdrop failed: {error}")
        if not self._backdrop_applied:
            append_diagnostic("Backdrop unavailable; using translucent fallback. " + "; ".join(failures))

    def toggle(self):
        if self.isVisible() and not self.animating:
            self.hide_popup()
        elif not self.animating:
            self.show_popup()

    def show_popup(self):
        self.center_on_current_screen()
        self.setWindowOpacity(0.90)
        self.show()
        self.raise_()
        self.activateWindow()
        self.search.clear()
        self.search.setFocus(Qt.PopupFocusReason)
        self.relayout()
        self._animate_popup(opening=True)

    def hide_popup(self):
        if not self.isVisible() or self.animating:
            return
        self._animate_popup(opening=False)

    def _animate_popup(self, opening: bool):
        self.animating = True
        opacity = QPropertyAnimation(self, b"windowOpacity", self)
        opacity.setDuration(95 if opening else 70)
        opacity.setStartValue(0.90 if opening else 1.0)
        opacity.setEndValue(1.0 if opening else 0.94)
        opacity.setEasingCurve(QEasingCurve.OutCubic if opening else QEasingCurve.InCubic)
        self._popup_animation = opacity
        opacity.finished.connect(lambda: self._animation_finished(opening))
        opacity.start(QPropertyAnimation.DeleteWhenStopped)

    def _animation_finished(self, opening: bool):
        self.animating = False
        self._popup_animation = None
        if not opening:
            self.hide()
            self.setWindowOpacity(1.0)

    def open_software_manager(self):
        if self._popup_animation is not None:
            self._popup_animation.stop()
            self._popup_animation = None
        self.animating = False
        self.hide()
        self.setWindowOpacity(1.0)
        self.settings_requested.emit()

    def position_resize_handles(self):
        border = RESIZE_BORDER
        corner = RESIZE_BORDER * 2
        width = self.width()
        height = self.height()
        self.resize_handles["top"].setGeometry(corner, 0, max(0, width - corner * 2), border)
        self.resize_handles["bottom"].setGeometry(corner, height - border, max(0, width - corner * 2), border)
        self.resize_handles["left"].setGeometry(0, corner, border, max(0, height - corner * 2))
        self.resize_handles["right"].setGeometry(width - border, corner, border, max(0, height - corner * 2))
        self.resize_handles["top_left"].setGeometry(0, 0, corner, corner)
        self.resize_handles["top_right"].setGeometry(width - corner, 0, corner, corner)
        self.resize_handles["bottom_left"].setGeometry(0, height - corner, corner, corner)
        self.resize_handles["bottom_right"].setGeometry(width - corner, height - corner, corner, corner)

    def event(self, event):
        if event.type() == QEvent.WindowDeactivate and self.isVisible() and not self.animating:
            QTimer.singleShot(0, self.hide_popup)
        return super().event(event)

    def eventFilter(self, watched, event):
        if watched is self.search and event.type() == QEvent.KeyPress:
            if event.key() == Qt.Key_Escape:
                self.hide_popup()
                return True
            if event.key() in (Qt.Key_Return, Qt.Key_Enter) and self.visible_buttons:
                open_path(self.visible_buttons[0].item.path)
                self.hide_popup()
                return True
            if event.key() == Qt.Key_Down and self.visible_buttons:
                self.visible_buttons[0].setFocus()
                return True
        if event.type() == QEvent.MouseButtonPress and self.isVisible():
            local = self.mapFromGlobal(self.cursor_pos())
            if not self.rect().contains(local):
                self.hide_popup()
                return True
        return super().eventFilter(watched, event)

    def keyPressEvent(self, event: QKeyEvent):
        if event.key() == Qt.Key_Escape:
            self.hide_popup()
            event.accept()
            return
        super().keyPressEvent(event)

    def resizeEvent(self, event):
        self.panel.setGeometry(self.rect().adjusted(RESIZE_BORDER, RESIZE_BORDER, -RESIZE_BORDER, -RESIZE_BORDER))
        self.position_resize_handles()
        self.relayout()
        super().resizeEvent(event)


class AppTray(QApplication):
    def __init__(self, argv: list[str]):
        super().__init__(argv)
        self.already_running = False
        self._instance_guard = QSharedMemory(INSTANCE_SERVER_NAME, self)
        if not self._instance_guard.create(1):
            self._notify_existing_instance()
            self.already_running = True
            return
        self.instance_server = QLocalServer(self)
        QLocalServer.removeServer(INSTANCE_SERVER_NAME)
        if self.instance_server.listen(INSTANCE_SERVER_NAME):
            self.instance_server.newConnection.connect(self._accept_instance_connection)
        else:
            append_diagnostic(f"Could not start instance server: {self.instance_server.errorString()}")
        self.setApplicationName(APP_NAME)
        self.setApplicationVersion(APP_VERSION)
        self.setQuitOnLastWindowClosed(False)
        self.config = load_config()
        self.icon = create_app_icon()
        self.window = LauncherWindow(self.config)
        self.window.settings_requested.connect(self.open_settings)
        self.settings_dialog: SettingsDialog | None = None
        self.hotkey_filter = HotkeyFilter(self.toggle_window_from_hotkey)
        self.installNativeEventFilter(self.hotkey_filter)
        self.hotkey_registered = False
        self.hotkey_blocked = False
        self._polled_hotkey_down = False
        self._last_hotkey_toggle = 0.0
        self.hotkey_poll_timer = QTimer(self)
        self.hotkey_poll_timer.setInterval(35)
        self.hotkey_poll_timer.timeout.connect(self.poll_hotkey_fallback)
        self.hotkey_poll_timer.start()

        self.tray = QSystemTrayIcon(self.icon, self)
        self.tray.setToolTip(self._tray_tooltip())
        self.tray.setContextMenu(self.build_menu())
        self.tray.activated.connect(self.on_tray_activated)
        self.tray.show()
        self.sync_hotkey_registration()
        self.apply_auto_start_setting()
        if not self.config.shortcuts:
            QTimer.singleShot(250, self.open_settings)

    def _notify_existing_instance(self):
        socket = QLocalSocket(self)
        socket.connectToServer(INSTANCE_SERVER_NAME)
        if socket.waitForConnected(700):
            socket.write(b"show")
            socket.waitForBytesWritten(700)
            socket.disconnectFromServer()

    def _accept_instance_connection(self):
        while self.instance_server.hasPendingConnections():
            socket = self.instance_server.nextPendingConnection()
            socket.readyRead.connect(lambda socket=socket: self._read_instance_command(socket))
            if socket.bytesAvailable():
                self._read_instance_command(socket)

    def _read_instance_command(self, socket: QLocalSocket):
        if bytes(socket.readAll()).strip() == b"show":
            if self.window.isVisible():
                self.window.raise_()
                self.window.activateWindow()
            else:
                self.window.show_popup()
        socket.disconnectFromServer()

    def _tray_tooltip(self) -> str:
        return f"{APP_NAME}\n快捷键: {format_hotkey(self.config.hotkey_modifiers, self.config.hotkey_key)}"

    def build_menu(self) -> QMenu:
        menu = QMenu()
        settings = QAction("设置", self)
        settings.triggered.connect(self.open_settings)
        shortcut = QAction(f"快捷键: {format_hotkey(self.config.hotkey_modifiers, self.config.hotkey_key)}", self)
        shortcut.setEnabled(False)
        startup = QAction(f"开机自启: {'已开启' if self.config.auto_start else '未开启'}", self)
        startup.setEnabled(False)
        blocked = QAction("屏蔽快捷键", self)
        blocked.setCheckable(True)
        blocked.setChecked(self.hotkey_blocked)
        blocked.toggled.connect(self.set_hotkey_blocked)
        restart = QAction("重启", self)
        restart.triggered.connect(self.restart_app)
        quit_action = QAction("退出", self)
        quit_action.triggered.connect(self.quit_app)
        menu.addAction(settings)
        menu.addAction(shortcut)
        menu.addAction(startup)
        menu.addAction(blocked)
        menu.addAction(restart)
        menu.addSeparator()
        menu.addAction(quit_action)
        return menu

    def open_settings(self):
        if self.settings_dialog and self.settings_dialog.isVisible():
            self.settings_dialog.raise_()
            self.settings_dialog.activateWindow()
            return
        self.settings_dialog = SettingsDialog(self.config)
        self.unregister_hotkey()
        try:
            if self.settings_dialog.exec():
                self.config = self.settings_dialog.config
                save_config(self.config)
                self.window.config = self.config
                self.window.refresh_shortcuts()
                self.tray.setToolTip(self._tray_tooltip())
                self.tray.setContextMenu(self.build_menu())
                self.apply_auto_start_setting()
        finally:
            self.settings_dialog = None
            self.sync_hotkey_registration()

    def on_tray_activated(self, reason):
        if reason == QSystemTrayIcon.Trigger:
            self.toggle_window_from_hotkey()

    def restart_app(self):
        save_config(self.config)
        QApplication.exit(RESTART_EXIT_CODE)

    def quit_app(self):
        self.unregister_hotkey()
        self.tray.hide()
        self.quit()

    def set_hotkey_blocked(self, blocked: bool):
        self.hotkey_blocked = blocked
        if blocked:
            self.unregister_hotkey()
        else:
            self.sync_hotkey_registration()

    def sync_hotkey_registration(self):
        self.unregister_hotkey()
        if not self.hotkey_blocked:
            self.register_hotkey()

    def register_hotkey(self):
        if self.hotkey_registered or user32 is None:
            return
        mask = 0
        for modifier in self.config.hotkey_modifiers:
            mask |= MODIFIER_FLAGS.get(modifier, 0)
        key = VIRTUAL_KEYS.get(self.config.hotkey_key, VIRTUAL_KEYS["Space"])
        try:
            self.hotkey_registered = bool(user32.RegisterHotKey(None, HOTKEY_ID, mask, key))
        except OSError:
            self.hotkey_registered = False

    def unregister_hotkey(self):
        if not self.hotkey_registered or user32 is None:
            return
        try:
            user32.UnregisterHotKey(None, HOTKEY_ID)
        finally:
            self.hotkey_registered = False

    def toggle_window_from_hotkey(self):
        now = time.monotonic()
        if now - self._last_hotkey_toggle < 0.16:
            return
        self._last_hotkey_toggle = now
        self.window.toggle()

    def poll_hotkey_fallback(self):
        if self.hotkey_blocked or user32 is None:
            self._polled_hotkey_down = False
            return
        virtual_key = VIRTUAL_KEYS.get(self.config.hotkey_key, VIRTUAL_KEYS["Space"])
        keys = [MODIFIER_VKEYS[item] for item in self.config.hotkey_modifiers if item in MODIFIER_VKEYS]
        keys.append(virtual_key)
        pressed = all(bool(user32.GetAsyncKeyState(key) & 0x8000) for key in keys)
        if pressed and not self._polled_hotkey_down and time.monotonic() - self._last_hotkey_toggle >= 0.16:
            self.toggle_window_from_hotkey()
        self._polled_hotkey_down = pressed

    def get_launch_args(self) -> list[str]:
        if getattr(sys, "frozen", False):
            return [sys.executable]
        script = Path(__file__).resolve().parent.parent / "main.py"
        executable = Path(sys.executable)
        pythonw = executable.with_name("pythonw.exe")
        if executable.name.lower() == "python.exe" and pythonw.exists():
            return [str(pythonw), str(script)]
        return [sys.executable, str(script)]

    def apply_auto_start_setting(self):
        if winreg is None:
            return
        run_key = r"Software\Microsoft\Windows\CurrentVersion\Run"
        try:
            with winreg.CreateKey(winreg.HKEY_CURRENT_USER, run_key) as key:
                if self.config.auto_start:
                    winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, list2cmdline(self.get_launch_args()))
                else:
                    try:
                        winreg.DeleteValue(key, APP_NAME)
                    except FileNotFoundError:
                        pass
        except OSError:
            pass


def run():
    install_exception_logger()
    app = AppTray(sys.argv)
    if app.already_running:
        return
    code = app.exec()
    if code == RESTART_EXIT_CODE:
        app.instance_server.close()
        if app._instance_guard.isAttached():
            app._instance_guard.detach()
        Popen(
            app.get_launch_args(),
            creationflags=DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP,
            close_fds=True,
            cwd=str(Path.cwd()),
            env=os.environ.copy(),
        )
