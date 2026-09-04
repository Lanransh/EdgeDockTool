from __future__ import annotations

import os
import sys
from ctypes import Structure, WinDLL, byref, c_int, c_size_t, c_void_p, cast, sizeof, wintypes
from pathlib import Path
from subprocess import CREATE_NEW_PROCESS_GROUP, DETACHED_PROCESS, Popen, list2cmdline

try:
    import winreg
except ImportError:  # pragma: no cover - only used on Windows
    winreg = None

from PySide6.QtCore import (
    QAbstractNativeEventFilter,
    QEvent,
    QFileInfo,
    QPoint,
    QPropertyAnimation,
    QRect,
    QSize,
    Qt,
    QTimer,
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
    QGraphicsDropShadowEffect,
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

from .config import AppConfig, ShortcutItem, load_config, save_config
from .utils import display_name, item_kind, open_path

APP_NAME = "EdgeDockTool"
APP_VERSION = "0.2.0"
RESTART_EXIT_CODE = 42
HOTKEY_ID = 1
WM_HOTKEY = 0x0312
MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004

MODIFIER_FLAGS = {"Ctrl": MOD_CONTROL, "Alt": MOD_ALT, "Shift": MOD_SHIFT}
VIRTUAL_KEYS = {"Space": 0x20, **{f"F{i}": 0x6F + i for i in range(1, 13)}}
VIRTUAL_KEYS.update({chr(code): code for code in range(ord("A"), ord("Z") + 1)})

if os.name == "nt":
    user32 = WinDLL("user32", use_last_error=True)
else:  # pragma: no cover - allows importing the module for tooling on other platforms
    user32 = None


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
        if event_type != b"windows_generic_MSG":
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
        self.setAlternatingRowColors(True)
        self.setStyleSheet(
            """
            QListWidget { background: #f8fafc; border: 1px solid #cbd5e1; border-radius: 10px; padding: 6px; }
            QListWidget::item { padding: 8px; border-radius: 6px; }
            QListWidget::item:selected { background: #1e293b; color: white; }
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
        list_item = QListWidgetItem(f"{item.name}  [{item.kind}]")
        list_item.setToolTip(item.path)
        list_item.setData(Qt.UserRole, item)
        if row is None:
            self.addItem(list_item)
        else:
            self.insertItem(row, list_item)

    def shortcuts(self) -> list[ShortcutItem]:
        return [
            item.data(Qt.UserRole)
            for index in range(self.count())
            if isinstance((item := self.item(index)).data(Qt.UserRole), ShortcutItem)
        ]


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
            QDialog { background: #f1f5f9; }
            QLabel#title { font-size: 25px; font-weight: 700; color: #0f172a; }
            QLabel#subtitle { color: #475569; }
            QPushButton { background: #0f172a; color: white; border: none; border-radius: 7px; padding: 9px 14px; }
            QPushButton:hover { background: #334155; }
            QLineEdit { background: white; border: 1px solid #cbd5e1; border-radius: 7px; padding: 8px; }
            """
        )

        title = QLabel("快捷入口设置", self)
        title.setObjectName("title")
        subtitle = QLabel("按 Alt + Space 呼出中央启动器，可拖拽文件、文件夹或程序到列表。", self)
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
        remove = QPushButton("删除选中")
        remove.clicked.connect(self.remove_selected)
        save = QPushButton("保存并关闭")
        save.clicked.connect(self.accept)

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
            QToolButton { color: #e5e7eb; background: transparent; border: 1px solid transparent; border-radius: 10px; padding: 8px 5px; font-size: 13px; }
            QToolButton:hover, QToolButton:focus { background: rgba(255,255,255,0.10); border: 1px solid rgba(147,197,253,0.9); }
            QToolButton:pressed { background: rgba(96,165,250,0.24); }
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


class LauncherWindow(QWidget):
    def __init__(self, config: AppConfig):
        super().__init__()
        self.config = config
        self.visible_buttons: list[ShortcutButton] = []
        self.buttons: list[ShortcutButton] = []
        self.animating = False
        self._backdrop_applied = False
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Tool | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setMinimumSize(540, 360)
        self.setWindowTitle(APP_NAME)
        self.setStyleSheet("QWidget#panel { background: rgba(20, 20, 22, 224); border: 1px solid rgba(255,255,255,0.18); border-radius: 18px; }")

        self.panel = QFrame(self)
        self.panel.setObjectName("panel")
        shadow = QGraphicsDropShadowEffect(self.panel)
        shadow.setBlurRadius(42)
        shadow.setOffset(0, 12)
        shadow.setColor(QColor(0, 0, 0, 145))
        self.panel.setGraphicsEffect(shadow)

        self.search = QLineEdit(self.panel)
        self.search.setPlaceholderText("Search applications...")
        self.search.setClearButtonEnabled(True)
        self.search.setFixedWidth(430)
        self.search.setAlignment(Qt.AlignLeft)
        self.search.setStyleSheet(
            "QLineEdit { background: rgba(5,5,6,0.80); color: #f8fafc; border: 1px solid rgba(148,163,184,0.42); border-radius: 22px; padding: 11px 16px; font-size: 14px; } QLineEdit:focus { border: 1px solid #93c5fd; }"
        )
        self.search.installEventFilter(self)
        self.search.textChanged.connect(self.relayout)

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
        layout.setContentsMargins(28, 22, 28, 24)
        layout.setSpacing(12)
        header = QHBoxLayout()
        header.addStretch(1)
        header.addWidget(self.search)
        header.addStretch(1)
        layout.addLayout(header)
        layout.addWidget(self.status)
        layout.addWidget(self.scroll, 1)

        self.refresh_shortcuts()
        self.resize_to_screen()
        self.panel.setGeometry(self.rect())
        app = QApplication.instance()
        if app is not None:
            app.installEventFilter(self)

    def resize_to_screen(self):
        screen = QApplication.screenAt(self.cursor_pos()) or QApplication.primaryScreen()
        geo = screen.availableGeometry()
        width = min(max(760, int(geo.width() * 0.94)), max(540, geo.width() - 36))
        height = min(max(470, int(geo.height() * 0.88)), max(360, geo.height() - 36))
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
        columns = max(1, min(8, (max(1, self.scroll.viewport().width()) + 16) // 128))
        for index, button in enumerate(self.visible_buttons):
            self.grid.addWidget(button, index // columns, index % columns)
            button.show()
        for button in self.buttons:
            if button not in self.visible_buttons:
                button.hide()
        self.empty.setText("没有匹配的应用" if self.buttons and query else "还没有快捷入口\n打开设置后拖入文件、文件夹或程序")
        self.empty.setVisible(not self.visible_buttons)
        if self.empty.isVisible():
            self.grid.addWidget(self.empty, 0, 0, 1, max(1, columns))
        hotkey = format_hotkey(self.config.hotkey_modifiers, self.config.hotkey_key)
        self.status.setText(f"{len(self.visible_buttons)} 个应用   •   {hotkey} 呼出")

    def showEvent(self, event):
        super().showEvent(event)
        if not self._backdrop_applied:
            self._apply_backdrop()

    def _apply_backdrop(self):
        handle = self.windowHandle()
        if handle is None or os.name != "nt":
            return
        hwnd = None
        try:
            hwnd = int(handle.winId())
            dwm = WinDLL("dwmapi", use_last_error=True)
            backdrop = c_int(3)  # DWMSBT_TRANSIENTWINDOW (acrylic-like)
            corner = c_int(2)  # DWMWCP_ROUND
            backdrop_result = dwm.DwmSetWindowAttribute(hwnd, 38, byref(backdrop), 4)
            dwm.DwmSetWindowAttribute(hwnd, 33, byref(corner), 4)
            if backdrop_result == 0:
                self._backdrop_applied = True
                return
        except (OSError, AttributeError):
            pass

        # Windows 10 and older Windows 11 builds do not expose the system
        # backdrop attribute, so use the legacy acrylic composition API.
        if user32 is None or hwnd is None:
            return
        try:
            policy = ACCENT_POLICY(4, 0, 0xCC141416, 0)  # ACCENT_ENABLE_ACRYLICBLURBEHIND
            data = WINDOWCOMPOSITIONATTRIBDATA(19, cast(byref(policy), c_void_p), c_size_t(sizeof(policy)))
            self._backdrop_applied = bool(user32.SetWindowCompositionAttribute(hwnd, byref(data)))
        except (OSError, AttributeError):
            self._backdrop_applied = False

    def toggle(self):
        if self.isVisible() and not self.animating:
            self.hide_popup()
        elif not self.animating:
            self.show_popup()

    def show_popup(self):
        self.resize_to_screen()
        target = QRect(self.geometry())
        start = QRect(target)
        start.setWidth(max(300, int(target.width() * 0.94)))
        start.setHeight(max(240, int(target.height() * 0.94)))
        start.moveCenter(target.center())
        self.setGeometry(start)
        self.setWindowOpacity(0.0)
        self.show()
        self.raise_()
        self.activateWindow()
        self.search.clear()
        self.search.setFocus(Qt.PopupFocusReason)
        self.relayout()
        self._animate_popup(start, target, opening=True)

    def hide_popup(self):
        if not self.isVisible() or self.animating:
            return
        start = QRect(self.geometry())
        end = QRect(start)
        end.setWidth(max(300, int(start.width() * 0.94)))
        end.setHeight(max(240, int(start.height() * 0.94)))
        end.moveCenter(start.center())
        self._animate_popup(start, end, opening=False)

    def _animate_popup(self, start: QRect, end: QRect, opening: bool):
        self.animating = True
        geometry = QPropertyAnimation(self, b"geometry", self)
        geometry.setDuration(180)
        geometry.setStartValue(start)
        geometry.setEndValue(end)
        opacity = QPropertyAnimation(self, b"windowOpacity", self)
        opacity.setDuration(180)
        opacity.setStartValue(0.0 if opening else 1.0)
        opacity.setEndValue(1.0 if opening else 0.0)
        self._popup_animations = (geometry, opacity)
        geometry.finished.connect(lambda: self._animation_finished(opening))
        geometry.start(QPropertyAnimation.DeleteWhenStopped)
        opacity.start(QPropertyAnimation.DeleteWhenStopped)

    def _animation_finished(self, opening: bool):
        self.animating = False
        if not opening:
            self.hide()
            self.setWindowOpacity(1.0)

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

    def changeEvent(self, event):
        if event.type() == QEvent.WindowDeactivate and self.isVisible() and not self.animating:
            self.hide_popup()
        super().changeEvent(event)

    def resizeEvent(self, event):
        self.panel.setGeometry(self.rect())
        self.relayout()
        super().resizeEvent(event)


class AppTray(QApplication):
    def __init__(self, argv: list[str]):
        super().__init__(argv)
        self.setApplicationName(APP_NAME)
        self.setApplicationVersion(APP_VERSION)
        self.setQuitOnLastWindowClosed(False)
        self.config = load_config()
        self.icon = create_app_icon()
        self.window = LauncherWindow(self.config)
        self.settings_dialog: SettingsDialog | None = None
        self.hotkey_filter = HotkeyFilter(self.toggle_window_from_hotkey)
        self.installNativeEventFilter(self.hotkey_filter)
        self.hotkey_registered = False
        self.hotkey_blocked = False

        self.tray = QSystemTrayIcon(self.icon, self)
        self.tray.setToolTip(self._tray_tooltip())
        self.tray.setContextMenu(self.build_menu())
        self.tray.activated.connect(self.on_tray_activated)
        self.tray.show()
        self.sync_hotkey_registration()
        self.apply_auto_start_setting()
        if not self.config.shortcuts:
            QTimer.singleShot(250, self.open_settings)

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
        self.window.toggle()

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
    app = AppTray(sys.argv)
    code = app.exec()
    if code == RESTART_EXIT_CODE:
        Popen(
            app.get_launch_args(),
            creationflags=DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP,
            close_fds=True,
            cwd=str(Path.cwd()),
            env=os.environ.copy(),
        )
