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
    QMimeData,
    QPoint,
    QPropertyAnimation,
    QSize,
    Qt,
    QTimer,
    QSharedMemory,
    Signal,
)
from PySide6.QtGui import (
    QAction,
    QColor,
    QDrag,
    QDragEnterEvent,
    QDropEvent,
    QIcon,
    QKeyEvent,
    QPainter,
    QPixmap,
)
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QDialog,
    QFileIconProvider,
    QFrame,
    QGraphicsOpacityEffect,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QSizePolicy,
    QSystemTrayIcon,
    QToolButton,
    QVBoxLayout,
    QWidget,
)
from PySide6.QtNetwork import QLocalServer, QLocalSocket

from .config import AppConfig, ShortcutItem, load_config, save_config
from .shortcut_store import (
    add_shortcut,
    create_stack,
    ensure_shortcuts_dir,
    migrate_legacy_shortcuts,
    scan_shortcuts,
    storage_signature,
)
from .utils import open_path

APP_NAME = "EdgeDockTool"
APP_VERSION = "0.3.0"
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

INTERNAL_DRAG_MIME = "application/x-edgedock-entry"
PANEL_SIZES = {
    "small": QSize(640, 420),
    "medium": QSize(840, 540),
    "large": QSize(1080, 700),
}
_ICON_PROVIDER: QFileIconProvider | None = None
_ICON_CACHE: dict[tuple[str, str], QIcon] = {}

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
    if kind == "stack":
        for offset, color in ((4, "#64748b"), (0, "#dbeafe")):
            painter.setBrush(QColor(color))
            painter.drawRoundedRect(int(size * 0.16) + offset, int(size * 0.18) - offset, int(size * 0.66), int(size * 0.62), 10, 10)
        painter.setBrush(QColor("#60a5fa"))
        cell = int(size * 0.17)
        for x, y in ((0.27, 0.31), (0.52, 0.31), (0.27, 0.55), (0.52, 0.55)):
            painter.drawRoundedRect(int(size * x), int(size * y), cell, cell, 4, 4)
    elif kind == "folder":
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
    global _ICON_PROVIDER

    cache_key = (item.kind, item.path)
    cached = _ICON_CACHE.get(cache_key)
    if cached is not None:
        return cached
    if item.kind == "stack":
        icon = fallback_icon("stack")
    else:
        if _ICON_PROVIDER is None:
            _ICON_PROVIDER = QFileIconProvider()
        icon = _ICON_PROVIDER.icon(QFileInfo(item.path))
        if icon.isNull():
            icon = fallback_icon(item.kind)
    if len(_ICON_CACHE) >= 256:
        _ICON_CACHE.clear()
    _ICON_CACHE[cache_key] = icon
    return icon


class SettingsDialog(QDialog):
    def __init__(self, config: AppConfig, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle(f"{APP_NAME} 设置")
        self.setFixedSize(540, 390)
        self.config = AppConfig(
            auto_start=config.auto_start,
            hotkey_modifiers=list(config.hotkey_modifiers),
            hotkey_key=config.hotkey_key,
            drag_mode=config.drag_mode,
            panel_size=config.panel_size,
            shortcuts=list(config.shortcuts),
        )
        self.setStyleSheet(
            """
            QDialog { background: #f5f7fb; }
            QLabel#title { font-size: 24px; font-weight: 700; color: #172033; }
            QLabel#subtitle { color: #66758d; font-size: 12px; }
            QFrame#card { background: white; border: 1px solid #dbe3ee; border-radius: 12px; }
            QLabel#sectionTitle { color: #27364d; font-size: 14px; font-weight: 600; }
            QLabel#sectionHint { color: #7a879a; font-size: 11px; }
            QPushButton { background: white; color: #334155; border: 1px solid #d7e0eb; border-radius: 8px; padding: 9px 14px; }
            QPushButton:hover { background: #f8fafc; border-color: #aebcd0; }
            QPushButton#primary { background: #2563eb; color: white; border-color: #2563eb; font-weight: 600; }
            QPushButton#primary:hover { background: #1d4ed8; }
            QCheckBox { color: #41516a; spacing: 7px; }
            QRadioButton { color: #41516a; spacing: 7px; padding: 5px; }
            """
        )

        title = QLabel("启动面板设置", self)
        title.setObjectName("title")
        subtitle = QLabel("开启拖动模式后，可直接添加项目或将两个图标合并为堆叠。", self)
        subtitle.setObjectName("subtitle")

        self.drag_mode_checkbox = QCheckBox("可拖动模式", self)
        self.drag_mode_checkbox.setChecked(self.config.drag_mode)
        drag_hint = QLabel("允许从资源管理器拖入项目，也允许图标互拖创建堆叠", self)
        drag_hint.setObjectName("sectionHint")

        size_title = QLabel("面板大小", self)
        size_title.setObjectName("sectionTitle")
        self.size_buttons = {}
        size_row = QHBoxLayout()
        for key, label in (("small", "小"), ("medium", "中"), ("large", "大")):
            button = QRadioButton(label, self)
            button.setChecked(key == self.config.panel_size)
            self.size_buttons[key] = button
            size_row.addWidget(button)
        size_row.addStretch(1)

        open_folder = QPushButton("打开快捷入口文件夹")
        open_folder.clicked.connect(self.open_shortcuts_folder)
        save = QPushButton("保存并关闭")
        save.setObjectName("primary")
        save.clicked.connect(self.save_and_close)

        buttons = QHBoxLayout()
        buttons.addWidget(open_folder)
        buttons.addStretch(1)
        buttons.addWidget(save)

        card = QFrame(self)
        card.setObjectName("card")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(18, 17, 18, 17)
        card_layout.setSpacing(10)
        card_layout.addWidget(self.drag_mode_checkbox)
        card_layout.addWidget(drag_hint)
        card_layout.addSpacing(8)
        card_layout.addWidget(size_title)
        card_layout.addLayout(size_row)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(26, 24, 26, 24)
        layout.setSpacing(13)
        layout.addWidget(title)
        layout.addWidget(subtitle)
        layout.addWidget(card, 1)
        layout.addLayout(buttons)

    def open_shortcuts_folder(self):
        open_path(str(ensure_shortcuts_dir()))

    def save_and_close(self):
        self.config.drag_mode = self.drag_mode_checkbox.isChecked()
        self.config.panel_size = next(key for key, button in self.size_buttons.items() if button.isChecked())
        self.accept()


class ShortcutButton(QToolButton):
    def __init__(self, item: ShortcutItem, drag_mode: bool, parent: QWidget | None = None):
        super().__init__(parent)
        self.item = item
        self.drag_mode = drag_mode
        self.base_icon_size = 64
        self._press_position: QPoint | None = None
        self._icon_animation: QPropertyAnimation | None = None
        self.setText(item.name)
        self.setToolButtonStyle(Qt.ToolButtonTextUnderIcon)
        self.setIcon(icon_for_item(item))
        self.setIconSize(QSize(self.base_icon_size, self.base_icon_size))
        self.setFixedSize(116, 112)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setAcceptDrops(drag_mode)
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
        if self._icon_animation is not None:
            self._icon_animation.stop()
        animation = QPropertyAnimation(self, b"iconSize", self)
        animation.setDuration(190)
        animation.setStartValue(self.iconSize())
        animation.setKeyValueAt(0.28, QSize(56, 56))
        animation.setKeyValueAt(0.68, QSize(69, 69))
        animation.setEndValue(QSize(self.base_icon_size, self.base_icon_size))
        animation.setEasingCurve(QEasingCurve.OutCubic)
        self._icon_animation = animation
        animation.finished.connect(lambda: setattr(self, "_icon_animation", None))
        animation.start(QPropertyAnimation.DeleteWhenStopped)
        QTimer.singleShot(140, self._launch_after_animation)

    def _launch_after_animation(self):
        window = self.window()
        if self.item.kind == "stack":
            open_stack = getattr(window, "open_stack", None)
            if callable(open_stack):
                open_stack(self.item)
            return
        open_path(self.item.path)
        hide_popup = getattr(window, "hide_popup", None)
        if callable(hide_popup):
            hide_popup()

    def _animate_hover(self, size: int):
        if self._icon_animation is not None:
            self._icon_animation.stop()
        animation = QPropertyAnimation(self, b"iconSize", self)
        animation.setDuration(115)
        animation.setStartValue(self.iconSize())
        animation.setEndValue(QSize(size, size))
        animation.setEasingCurve(QEasingCurve.OutCubic)
        self._icon_animation = animation
        animation.finished.connect(lambda: setattr(self, "_icon_animation", None))
        animation.start(QPropertyAnimation.DeleteWhenStopped)

    def enterEvent(self, event):
        self._animate_hover(70)
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._animate_hover(self.base_icon_size)
        super().leaveEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._press_position = event.position().toPoint()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if (
            self.drag_mode
            and self.item.kind != "stack"
            and self._press_position is not None
            and event.buttons() & Qt.LeftButton
            and (event.position().toPoint() - self._press_position).manhattanLength() >= QApplication.startDragDistance()
        ):
            mime = QMimeData()
            mime.setData(INTERNAL_DRAG_MIME, self.item.storage_path.encode("utf-8"))
            drag = QDrag(self)
            drag.setMimeData(mime)
            drag.setPixmap(self.icon().pixmap(52, 52))
            drag.setHotSpot(QPoint(26, 26))
            self._press_position = None
            drag.exec(Qt.MoveAction)
            self.setDown(False)
            return
        super().mouseMoveEvent(event)

    def dragEnterEvent(self, event: QDragEnterEvent):
        if self.drag_mode and event.mimeData().hasFormat(INTERNAL_DRAG_MIME):
            event.acceptProposedAction()
            return
        super().dragEnterEvent(event)

    def dropEvent(self, event: QDropEvent):
        if self.drag_mode and event.mimeData().hasFormat(INTERNAL_DRAG_MIME):
            source = bytes(event.mimeData().data(INTERNAL_DRAG_MIME)).decode("utf-8")
            stack_items = getattr(self.window(), "stack_items", None)
            if callable(stack_items) and stack_items(source, self.item.storage_path):
                event.acceptProposedAction()
                return
        super().dropEvent(event)

    def keyPressEvent(self, event: QKeyEvent):
        if event.key() == Qt.Key_Escape:
            handle_escape = getattr(self.window(), "handle_escape", None)
            if callable(handle_escape):
                handle_escape()
                event.accept()
                return
        super().keyPressEvent(event)


class LauncherWindow(QWidget):
    settings_requested = Signal()

    def __init__(self, config: AppConfig):
        super().__init__()
        self.config = config
        self.visible_buttons: list[ShortcutButton] = []
        self.buttons: list[ShortcutButton] = []
        self.current_stack_path: Path | None = None
        self.animating = False
        self._backdrop_applied = False
        self._backdrop_attempted = False
        self._popup_animation: QPropertyAnimation | None = None
        self._content_animation: QPropertyAnimation | None = None
        self._layout_signature = None
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Tool | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
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

        self.manage_button = QPushButton("⚙  设置", self.panel)
        self.manage_button.setCursor(Qt.PointingHandCursor)
        self.manage_button.setToolTip("拖动模式、面板大小和管理文件夹")
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
        self.content_opacity = QGraphicsOpacityEffect(self.content)
        self.content_opacity.setOpacity(1.0)
        self.content.setGraphicsEffect(self.content_opacity)
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
        self.back_button = QToolButton(self.panel)
        self.back_button.setText("←")
        self.back_button.setCursor(Qt.PointingHandCursor)
        self.back_button.setToolTip("返回全部快捷入口")
        self.back_button.setStyleSheet(
            "QToolButton { color: #f8fafc; background: rgba(255,255,255,0.08); border: none; border-radius: 9px; padding: 7px 10px; font-size: 18px; } QToolButton:hover { background: rgba(255,255,255,0.16); }"
        )
        self.back_button.clicked.connect(self.close_stack)
        self.back_button.hide()
        header.addWidget(self.back_button)
        header.addWidget(self.title)
        header.addStretch(1)
        header.addWidget(self.search, 1)
        header.addWidget(self.manage_button)
        layout.addLayout(header)
        layout.addWidget(self.status)
        layout.addWidget(self.scroll, 1)

        self.setAcceptDrops(config.drag_mode)
        self.refresh_shortcuts()
        self.apply_panel_size()
        self.panel.setGeometry(self.rect())
        self._store_signature = storage_signature(self.current_directory())
        self.storage_timer = QTimer(self)
        self.storage_timer.setInterval(900)
        self.storage_timer.timeout.connect(self.refresh_if_storage_changed)
        app = QApplication.instance()
        if app is not None:
            app.installEventFilter(self)

    def apply_panel_size(self):
        screen = QApplication.screenAt(self.cursor_pos()) or QApplication.primaryScreen()
        geo = screen.availableGeometry()
        desired = PANEL_SIZES.get(self.config.panel_size, PANEL_SIZES["medium"])
        self.setFixedSize(min(desired.width(), geo.width() - 36), min(desired.height(), geo.height() - 36))

    def center_on_current_screen(self):
        self.apply_panel_size()
        screen = QApplication.screenAt(self.cursor_pos()) or QApplication.primaryScreen()
        geo = screen.availableGeometry()
        self.move(geo.left() + (geo.width() - self.width()) // 2, geo.top() + (geo.height() - self.height()) // 2)

    @staticmethod
    def cursor_pos() -> QPoint:
        from PySide6.QtGui import QCursor

        return QCursor.pos()

    def refresh_shortcuts(self):
        for button in self.buttons:
            button.deleteLater()
        directory = self.current_directory()
        if not directory.exists():
            self.current_stack_path = None
            directory = self.current_directory()
        items = scan_shortcuts(directory)
        self.buttons = [ShortcutButton(item, self.config.drag_mode, self.content) for item in items]
        self.title.setText(self.current_stack_path.name if self.current_stack_path else "快捷启动")
        self.back_button.setVisible(self.current_stack_path is not None)
        self._layout_signature = None
        self.relayout()
        self._store_signature = storage_signature(directory)

    def current_directory(self) -> Path:
        return self.current_stack_path or ensure_shortcuts_dir()

    def apply_config(self, config: AppConfig):
        self.config = config
        self.setAcceptDrops(config.drag_mode)
        self.apply_panel_size()
        self.refresh_shortcuts()

    def refresh_if_storage_changed(self):
        if not self.current_directory().exists():
            self.refresh_shortcuts()
            return
        signature = storage_signature(self.current_directory())
        if signature != self._store_signature:
            self.refresh_shortcuts()

    def relayout(self):
        query = self.search.text().strip().casefold()
        visible_buttons = [button for button in self.buttons if not query or query in button.item.name.casefold() or query in button.item.path.casefold()]
        columns = max(1, min(8, (max(1, self.scroll.viewport().width()) + 16) // 132))
        signature = (query, columns, tuple(id(button) for button in visible_buttons))
        if signature == self._layout_signature:
            return
        self._layout_signature = signature

        while self.grid.count():
            item = self.grid.takeAt(0)
            if item.widget() is not None:
                item.widget().setParent(self.content)
        self.visible_buttons = visible_buttons
        for index, button in enumerate(self.visible_buttons):
            self.grid.addWidget(button, index // columns, index % columns)
            button.show()
        for button in self.buttons:
            if button not in self.visible_buttons:
                button.hide()
        if self.buttons and query:
            empty_text = "没有匹配的项目"
        elif self.config.drag_mode:
            empty_text = "把软件、文件或文件夹拖到这里"
        else:
            empty_text = "这里还没有快捷入口\n在设置中开启可拖动模式，或打开管理文件夹"
        self.empty.setText(empty_text)
        self.empty.setVisible(not self.visible_buttons)
        if self.empty.isVisible():
            self.grid.addWidget(self.empty, 0, 0, 1, max(1, columns))
        hotkey = format_hotkey(self.config.hotkey_modifiers, self.config.hotkey_key)
        mode = "可拖动模式" if self.config.drag_mode else f"{hotkey} 呼出"
        self.status.setText(f"{len(self.visible_buttons)} 个项目   •   {mode}")

    def showEvent(self, event):
        super().showEvent(event)
        self.refresh_if_storage_changed()
        self.storage_timer.start()
        if not self._backdrop_attempted:
            self._apply_backdrop()

    def hideEvent(self, event):
        self.storage_timer.stop()
        super().hideEvent(event)

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
            if self.current_stack_path is not None:
                self.current_stack_path = None
                self.refresh_shortcuts()

    def open_software_manager(self):
        if self._popup_animation is not None:
            self._popup_animation.stop()
            self._popup_animation = None
        self.animating = False
        self.hide()
        self.setWindowOpacity(1.0)
        if self.current_stack_path is not None:
            self.current_stack_path = None
            self.refresh_shortcuts()
        self.settings_requested.emit()

    def open_stack(self, item: ShortcutItem):
        stack_path = Path(item.storage_path)
        if stack_path.is_dir():
            self.transition_to_stack(stack_path)

    def close_stack(self):
        if self.current_stack_path is not None:
            root = ensure_shortcuts_dir()
            parent = self.current_stack_path.parent
            self.transition_to_stack(None if parent == root else parent)

    def transition_to_stack(self, stack_path: Path | None):
        if self._content_animation is not None:
            self._content_animation.stop()
        animation = QPropertyAnimation(self.content_opacity, b"opacity", self)
        animation.setDuration(80)
        animation.setStartValue(self.content_opacity.opacity())
        animation.setEndValue(0.0)
        animation.setEasingCurve(QEasingCurve.InCubic)
        self._content_animation = animation
        animation.finished.connect(lambda: self._finish_stack_transition(stack_path))
        animation.start(QPropertyAnimation.DeleteWhenStopped)

    def _finish_stack_transition(self, stack_path: Path | None):
        self.current_stack_path = stack_path
        self.search.blockSignals(True)
        self.search.clear()
        self.search.blockSignals(False)
        self.refresh_shortcuts()
        animation = QPropertyAnimation(self.content_opacity, b"opacity", self)
        animation.setDuration(150)
        animation.setStartValue(0.0)
        animation.setEndValue(1.0)
        animation.setEasingCurve(QEasingCurve.OutCubic)
        self._content_animation = animation
        animation.finished.connect(lambda: setattr(self, "_content_animation", None))
        animation.start(QPropertyAnimation.DeleteWhenStopped)

    def stack_items(self, source_storage: str, target_storage: str) -> bool:
        if create_stack(source_storage, target_storage) is None:
            return False
        self.refresh_shortcuts()
        return True

    def handle_escape(self):
        if self.current_stack_path is not None:
            self.close_stack()
        else:
            self.hide_popup()

    def dragEnterEvent(self, event: QDragEnterEvent):
        if self.config.drag_mode and event.mimeData().hasUrls():
            event.acceptProposedAction()
            return
        super().dragEnterEvent(event)

    def dragMoveEvent(self, event):
        if self.config.drag_mode and event.mimeData().hasUrls():
            event.acceptProposedAction()
            return
        super().dragMoveEvent(event)

    def dropEvent(self, event: QDropEvent):
        if self.config.drag_mode and event.mimeData().hasUrls():
            directory = self.current_directory()
            added = False
            for url in event.mimeData().urls():
                path = url.toLocalFile()
                if path and add_shortcut(path, directory) is not None:
                    added = True
            if added:
                self.refresh_shortcuts()
                event.acceptProposedAction()
                return
        super().dropEvent(event)

    def event(self, event):
        if event.type() == QEvent.WindowDeactivate and self.isVisible() and not self.animating and not self.config.drag_mode:
            QTimer.singleShot(0, self.hide_popup)
        return super().event(event)

    def eventFilter(self, watched, event):
        if watched is self.scroll.viewport() and event.type() == QEvent.Resize:
            self.relayout()
        if watched is self.search and event.type() == QEvent.KeyPress:
            if event.key() == Qt.Key_Escape:
                self.handle_escape()
                return True
            if event.key() in (Qt.Key_Return, Qt.Key_Enter) and self.visible_buttons:
                self.visible_buttons[0].click()
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
            self.handle_escape()
            event.accept()
            return
        super().keyPressEvent(event)

    def resizeEvent(self, event):
        self.panel.setGeometry(self.rect())
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
        ensure_shortcuts_dir()
        if migrate_legacy_shortcuts(self.config.shortcuts):
            self.config.shortcuts = []
            save_config(self.config)
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
        if not scan_shortcuts():
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
                self.window.apply_config(self.config)
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
