# -*- coding: utf-8 -*-
"""
应用入口 —— QApplication + 桌宠窗口 + 系统托盘。

支持运行时切换角色：
- 右键桌宠 →「切换角色」
- 托盘菜单 →「切换角色」
切换后会热加载对应形象的 webm，并保留位置/朝向等配置。
"""

from __future__ import annotations

import logging
import sys

from PySide6.QtCore import QTimer, QUrl
from PySide6.QtGui import QDesktopServices, QIcon
from PySide6.QtWidgets import QApplication, QMenu, QMessageBox, QSystemTrayIcon

from . import autostart as autostart_mod
from . import catalog
from .config import Config
from .kde import configure_platform
from .library import MovieLibrary
from .window import PetWindow


def _setup_logging(config: Config) -> None:
    config.dir.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        filename=str(config.dir / 'pet.log'),
        level=logging.INFO,
        format='%(asctime)s %(levelname)s %(message)s',
        encoding='utf-8',
    )


def _show_startup_error(title: str, message: str) -> None:
    QMessageBox.critical(None, title, message)


class PetApp:
    """管理桌宠窗口、托盘与角色热切换。"""

    def __init__(self, app: QApplication, config: Config) -> None:
        self.app = app
        self.config = config
        self.win: PetWindow | None = None
        self.tray: QSystemTrayIcon | None = None
        self._locked_action = None  # 托盘「锁定」项，用于反向同步勾选状态

    # ------------------------------------------------------------ 启动
    def start(self) -> None:
        character_id = str(self.config.get('character', catalog.DEFAULT_CHARACTER))
        available = catalog.list_available_characters()
        if character_id not in available and available:
            character_id = catalog.DEFAULT_CHARACTER if catalog.DEFAULT_CHARACTER in available else available[0]
            self.config.set('character', character_id)
            self.config.save()
        logging.info('当前形象: %s', character_id)
        self._create_ui(character_id)

    def _create_library(self, character_id: str) -> MovieLibrary:
        lib = MovieLibrary(character_id=character_id)
        logging.info('素材加载完成：%s %d 段动画', character_id, len(lib.names()))
        return lib

    def _create_ui(self, character_id: str) -> None:
        lib = self._create_library(character_id)
        win = PetWindow(lib, self.config)
        win.on_switch_character = self.switch_character
        win.on_rescan_characters = self.rescan_characters
        win.on_locked_changed = self.sync_locked_action
        win.show()

        tray = self._build_tray(win)

        # 清理旧对象（热切换时使用）
        old_win = self.win
        old_tray = self.tray
        self.win = win
        self.tray = tray

        if old_win is not None:
            old_win.hide()
            old_tray.hide() if old_tray is not None else None
            QTimer.singleShot(0, old_win.deleteLater)
            if old_tray is not None:
                QTimer.singleShot(0, old_tray.deleteLater)

        self.app.aboutToQuit.connect(win._save_position)

    # ------------------------------------------------------------ 角色切换
    def switch_character(self, character_id: str) -> None:
        if self.win is None:
            return
        current = str(self.config.get('character', catalog.DEFAULT_CHARACTER))
        if character_id == current:
            return

        # 先保存配置，即使后续加载失败也记住用户选择
        self.config.set('character', character_id)
        self.config.save()

        try:
            # 预创建新库，失败则保留当前角色
            lib = self._create_library(character_id)
        except Exception as exc:
            logging.exception('切换角色失败: %s', character_id)
            _show_startup_error('切换角色失败', str(exc))
            return

        logging.info('切换角色: %s -> %s', current, character_id)

        # 用新库创建新窗口/托盘，旧对象延迟销毁
        win = PetWindow(lib, self.config)
        win.on_switch_character = self.switch_character
        win.on_rescan_characters = self.rescan_characters
        win.on_locked_changed = self.sync_locked_action
        win.show()

        tray = self._build_tray(win)

        old_win = self.win
        old_tray = self.tray
        self.win = win
        self.tray = tray

        old_win.hide()
        if old_tray is not None:
            old_tray.hide()
        QTimer.singleShot(0, old_win.deleteLater)
        if old_tray is not None:
            QTimer.singleShot(0, old_tray.deleteLater)

        self.app.aboutToQuit.connect(win._save_position)

    # ------------------------------------------------------------ 角色重扫
    def rescan_characters(self) -> None:
        """重建托盘菜单，让新放入角色目录的形象立即可选（无需重启）。"""
        if self.win is None:
            return
        ids = catalog.list_available_characters()
        logging.info('角色目录重新扫描: %s', ids)
        old_tray = self.tray
        self.tray = self._build_tray(self.win)
        if old_tray is not None:
            old_tray.hide()
            QTimer.singleShot(0, old_tray.deleteLater)

    # ------------------------------------------------------------ 托盘
    def _build_tray(self, win: PetWindow) -> QSystemTrayIcon:
        tray = QSystemTrayIcon(QIcon(win.icon_pixmap()))

        def toggle_visible() -> None:
            if win.isVisible():
                win.hide()
            else:
                win.show()

        menu = QMenu()
        menu.addAction('显示 / 隐藏', toggle_visible)

        locked = menu.addAction('锁定（鼠标穿透）')
        locked.setCheckable(True)
        locked.setChecked(win.locked)
        locked.toggled.connect(win.set_locked)
        self._locked_action = locked

        m_char = menu.addMenu('切换角色')
        current = str(self.config.get('character', catalog.DEFAULT_CHARACTER))
        for cid in catalog.list_available_characters():
            act = m_char.addAction(cid)
            act.setCheckable(True)
            act.setChecked(cid == current)
            act.triggered.connect(lambda checked=False, cid=cid: self.switch_character(cid))
        m_char.addSeparator()
        m_char.addAction('打开角色文件夹…', self._open_characters_dir)
        m_char.addAction('重新扫描角色', self.rescan_characters)

        menu.addSeparator()

        physics = menu.addAction('拖动物理')
        physics.setCheckable(True)
        physics.setChecked(win.drag_physics)
        physics.toggled.connect(win.set_drag_physics)

        m_speed = menu.addMenu('播放速度')
        for sp in catalog.SPEED_STEPS:
            act = m_speed.addAction(f'{sp:g}x')
            act.setCheckable(True)
            act.setChecked(abs(win.speed - sp) < 0.02)
            act.triggered.connect(lambda checked=False, sp=sp: win.set_speed(sp))

        auto = menu.addAction('开机自启')
        auto.setCheckable(True)
        auto.setChecked(autostart_mod.is_enabled())
        auto.toggled.connect(autostart_mod.set_enabled)

        menu.addSeparator()
        menu.addAction('退出', self.app.quit)

        tray.setContextMenu(menu)
        tray.setToolTip('dsh-pet 独立桌宠')
        tray.activated.connect(
            lambda reason: toggle_visible()
            if reason == QSystemTrayIcon.ActivationReason.DoubleClick
            else None
        )
        tray.show()
        return tray

    def _open_characters_dir(self) -> None:
        """打开用户角色目录，方便一键安装的用户直接放入自定义角色。"""
        path = catalog.user_characters_dir()
        try:
            path.mkdir(parents=True, exist_ok=True)
        except OSError:
            logging.warning('创建角色目录失败: %s', path)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    def sync_locked_action(self, locked: bool) -> None:
        """把窗口的锁定状态回写到托盘勾选，避免与右键菜单不同步。

        setChecked 会再次触发 toggled → set_locked，用 blockSignals 断开回环。
        """
        action = self._locked_action
        if action is None or action.isChecked() == locked:
            return
        action.blockSignals(True)
        action.setChecked(locked)
        action.blockSignals(False)


def main(argv: list[str] | None = None) -> int:
    configure_platform()
    app = QApplication(argv if argv is not None else sys.argv)
    app.setApplicationName('dsh-pet-standalone')
    app.setQuitOnLastWindowClosed(False)

    config = Config()
    _setup_logging(config)
    logging.info('dsh-pet-standalone 启动')

    controller = PetApp(app, config)
    try:
        controller.start()
    except Exception as exc:
        logging.exception('启动失败')
        _show_startup_error('dsh-pet-standalone', str(exc))
        return 1

    logging.info('进入事件循环')
    return app.exec()


if __name__ == '__main__':
    sys.exit(main())
