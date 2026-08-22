# -*- coding: utf-8 -*-
"""诊断：验证窗口渲染链路（裁切、缩放、mask、当前帧）。

用法: python tests/diagnose.py
"""

from __future__ import annotations

import os
import sys

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from PySide6.QtWidgets import QApplication  # noqa: E402

from pet import catalog  # noqa: E402
from pet.config import Config  # noqa: E402
from pet.library import MovieLibrary  # noqa: E402
from pet.window import PetWindow  # noqa: E402


def main() -> int:
    video_dir = catalog.resolve_character_video_dir(catalog.DEFAULT_CHARACTER)
    if not video_dir.is_dir() or not any(video_dir.rglob('*.webm')):
        print(f'跳过：默认角色素材缺失 -> {video_dir}')
        return 0

    app = QApplication([])
    lib = MovieLibrary()
    cfg = Config(base=os.path.join(ROOT, 'tests', '_tmp_diag_fresh'))
    win = PetWindow(lib, cfg)
    win.show()
    app.processEvents()

    print('scale:', win.scale, '(DEFAULT_SCALE=', catalog.DEFAULT_SCALE, ')')
    print('内容裁切区:', f'{catalog.CONTENT_W}x{catalog.CONTENT_H}',
          f'@({catalog.CONTENT_X},{catalog.CONTENT_Y})')
    print('窗口 size:', win.width(), 'x', win.height())
    print('窗口 pos:', win.x(), win.y(), 'visible:', win.isVisible())
    print('使用原生 mask:', win._use_native_mask)
    br = win.mask().boundingRect()
    print('mask boundingRect:', br.x(), br.y(), br.width(), 'x', br.height())

    pm = win._frame_pixmap
    print('当前帧 pixmap:', None if pm is None else f'{pm.width()}x{pm.height()}')
    if pm is not None:
        img = pm.toImage()
        opaque = sum(
            1
            for y in range(0, img.height(), 5)
            for x in range(0, img.width(), 5)
            if img.pixelColor(x, y).alpha() > 0
        )
        print('当前帧非透明采样点:', opaque)

    grab = win.grab().toImage()
    visible = sum(
        1
        for y in range(0, grab.height(), 4)
        for x in range(0, grab.width(), 4)
        if grab.pixelColor(x, y).alpha() > 0
    )
    print('窗口 grab 非透明采样点:', visible,
          f'(grab {grab.width()}x{grab.height()})')

    clip = win.movie
    print('当前动画:', win.anim)
    print('帧数:', clip.frameCount() if clip else None,
          '当前帧号:', clip.currentFrameNumber() if clip else None,
          '速率:', clip.speed() if clip else None)
    src = clip.currentPixmap() if clip else None
    print('解码原始帧:', None if src is None or src.isNull() else f'{src.width()}x{src.height()}')
    win.close()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
