# dsh-pet-indesktop for KDE

基于 [PC2005-cloud/dsh-pet](https://github.com/PC2005-cloud/dsh-pet) 的动画素材与行为模型实现的独立桌面宠物。项目使用 Python、PySide6 和透明 WebM，支持 Windows、macOS 与 KDE Plasma。

原项目fork:[MerZlin/dsh-pet-indesktop Releases](https://github.com/MerZlin/dsh-pet-indesktop/releases)

> KDE 版本目前只在 Alpine Edge、KDE Plasma 6.7.4、Wayland + XWayland 环境中实机验证过。

## 安装

### KDE Plasma

无需克隆仓库，一行命令安装：

```sh
curl -fsSL https://raw.githubusercontent.com/LANqed/dsh-pet-kde/main/install-kde.sh | bash
```

安装器会下载程序和默认角色素材，检查或安装 Python、PySide6、imageio-ffmpeg、ffmpeg 与 XWayland，然后创建：

- 程序目录：`${XDG_DATA_HOME:-~/.local/share}/dsh-pet`
- 启动命令：`~/.local/bin/dsh-pet`
- KDE 应用菜单项：`dsh-pet`

安装器会按需将 `~/.local/bin` 注册到 `~/.profile`，重新打开终端后可以直接执行 `dsh-pet`。
当前终端尚未刷新环境时执行：

```sh
export PATH="$HOME/.local/bin:$PATH"
```

安装完成后会自动启动。建议执行前先查看[安装脚本](https://raw.githubusercontent.com/LANqed/dsh-pet-kde/main/install-kde.sh)内容。

不自动启动：

```sh
curl -fsSL https://raw.githubusercontent.com/LANqed/dsh-pet-kde/main/install-kde.sh | bash -s -- --no-launch
```

卸载并保留配置：

```sh
curl -fsSL https://raw.githubusercontent.com/LANqed/dsh-pet-kde/main/install-kde.sh | bash -s -- --uninstall
```

完全卸载，包括配置、日志和位置记录：

```sh
curl -fsSL https://raw.githubusercontent.com/LANqed/dsh-pet-kde/main/install-kde.sh | bash -s -- --uninstall --purge
```

卸载时会先移除 KDE/XDG 自启动项，再检查 dsh-pet 是否仍在运行。若程序尚未退出，安装文件不会删除；请从托盘退出后重新执行卸载命令。

安装器支持 Alpine、Debian/Ubuntu、Fedora、Arch 和 openSUSE 系列发行版。程序安装在用户目录；仅在缺少系统依赖时通过 `sudo` 调用包管理器。

### Windows 与 macOS

Windows 和 macOS 安装包由原项目发布，请前往：

**[MerZlin/dsh-pet-indesktop Releases](https://github.com/MerZlin/dsh-pet-indesktop/releases)**

| 平台 | 安装方式 |
| --- | --- |
| Windows | 下载 `.exe` 后直接运行 |
| macOS Apple Silicon | 下载 ZIP，解压后打开 `.app` |
| macOS Intel | 暂无预构建安装包，可按下文从源码运行 |

macOS 安装包未经过 Apple 公证。首次运行可右键 `.app` 选择“打开”，或在“系统设置 → 隐私与安全性”中放行。

## 功能

- 直接解码 640x360、24fps、VP9 alpha WebM，保留半透明边缘
- 30% 待机、10% 转向、40% 随机动作、20% 移动的链式动画状态机
- 自动移动、左右转向、点击回应和拖拽反馈
- 多角色自动发现与运行时热切换
- 透明无边框窗口、窗口置顶和多档缩放
- KDE 系统托盘与 XDG 开机自启
- 可从角色右键菜单锁定；锁定后窗口完全鼠标穿透，只能从托盘解锁
- 桌宠窗口不显示在 KDE 任务管理器 / 底部 dock（X11/XWayland 下请求跳过任务栏）
- 自动保存位置、朝向、缩放、置顶、移动开关和当前角色

## 使用

- 启动：执行 `dsh-pet`，或在 KDE 应用菜单中搜索 `dsh-pet`
- 退出：右键桌宠选择“退出”，或通过系统托盘退出
- 点击：待机状态下点击角色，随机播放点击回应
- 拖拽：按住角色移动超过 5px 后进入拖拽状态
- 右键菜单：只有角色当前可见像素响应右键，可选择动画、切换角色、复位位置、调整大小、锁定、切换置顶或开机自启
- 锁定：选择“锁定并穿透鼠标”后，整个窗口不再接收鼠标；通过托盘取消“锁定（鼠标穿透）”解锁

### KDE Wayland 说明

Wayland 不允许普通客户端自行设置顶层窗口坐标，因此原生 Wayland 下无法可靠实现自动移动和拖拽定位。程序检测到 KDE Wayland 后会默认使用 `QT_QPA_PLATFORM=xcb`，通过 XWayland 保留完整功能。

KDE/XWayland 下程序不调用 `QWidget.setMask()`。KWin 6 的 shape 合成路径会让透明窗口产生黑框、首帧残影和 WebM 切换闪烁，因此 KDE 使用无 shape 的完整透明 surface。代价是窗口透明区域仍属于输入区域；角色本体的点击、拖拽和右键菜单不受影响。

如需强制使用原生 Wayland：

```sh
QT_QPA_PLATFORM=wayland dsh-pet
```

此模式下窗口定位、自动移动和拖拽可能受限。

## 自定义角色

每个角色使用一个稳定的目录 ID：

```text
assets/characters/<角色ID>/videos/
├── idle/       # 待机
├── turn/       # 转向
├── move/       # 移动
├── click/      # 点击回应
├── drag/       # 拖拽，可选
└── random/     # 随机动作
```

`videos/` 也可以直接放置 WebM。程序会优先按子目录分类，其次读取 `manifest.json`，最后根据文件名关键词分类。

当前默认角色为 `deepseek-tan`：

```text
assets/characters/deepseek-tan/videos/
```

程序会扫描 `assets/characters/` 下所有包含 WebM 的角色，并将它们加入“切换角色”菜单。

### 外部角色

无需修改安装目录，可以将角色放到用户配置目录：

```text
Windows: %APPDATA%/dsh-pet-standalone/characters/<角色ID>/videos/
macOS:   ~/Library/Application Support/dsh-pet-standalone/characters/<角色ID>/videos/
Linux:   ${XDG_CONFIG_HOME:-~/.config}/dsh-pet-standalone/characters/<角色ID>/videos/
```

外部角色与内置角色 ID 相同时，优先使用外部版本。

### manifest.json

当文件名无法准确表达分类时，可在角色目录或 `videos/` 中添加：

```json
{
  "idle": "待机.webm",
  "turn": "转身.webm",
  "moves": ["走路.webm"],
  "clicks": ["点击回应.webm"],
  "drag": "拖拽.webm"
}
```

## 从源码运行

### Linux / KDE Plasma

```sh
git clone https://github.com/LANqed/dsh-pet-kde.git
cd dsh-pet-kde
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
python -m pet
```

Alpine 的 PyPI 通常没有适配 musl/Python 新版本的 PySide6 wheel，可改用系统包：

```sh
sudo apk add python3 py3-pyside6 py3-imageio-ffmpeg ffmpeg xwayland
python3 -m pet
```

### Windows / macOS

```sh
pip install -r requirements.txt
python -m pet
```

源码运行前需要确保 `assets/characters/<角色ID>/videos/` 中存在 WebM。远程 KDE 安装器在归档不含素材时，会从上游 dsh-pet 自动下载默认素材。

## 开机自启

菜单中的“开机自启”直接管理系统配置：

| 平台 | 实现 |
| --- | --- |
| Windows | `HKCU\Software\Microsoft\Windows\CurrentVersion\Run` |
| macOS | `~/Library/LaunchAgents/` |
| Linux/KDE | `${XDG_CONFIG_HOME:-~/.config}/autostart/com.merzlin.dsh-pet-standalone.desktop` |

## 技术实现

- `PySide6`：窗口、绘制、托盘和事件循环
- `imageio-ffmpeg`：后台线程解码 VP9 alpha WebM 为 RGBA
- `QTimer`：按视频帧率消费队列并驱动角色位置
- `ARGB32_Premultiplied`：避免 XWayland 透明 backing store 残影
- 每次播放使用独立停止事件，防止旧解码线程干扰新动画
- 解码队列满时等待消费，不主动丢弃动画帧

ffmpeg 必须在输入前指定 `libvpx-vp9`，否则可能丢失 WebM alpha：

```python
imageio_ffmpeg.read_frames(
    path,
    pix_fmt="rgba",
    bits_per_pixel=32,
    input_params=["-c:v", "libvpx-vp9"],
)
```

## 项目结构

```text
├── install-kde.sh          # KDE/Linux 一键安装器
├── pet/
│   ├── app.py              # 应用入口与系统托盘
│   ├── autostart.py        # 跨平台开机自启
│   ├── catalog.py          # 角色发现、动画分类与常量
│   ├── config.py           # 配置持久化
│   ├── kde.py              # Plasma Wayland/XWayland 兼容
│   ├── library.py          # WebM 素材库
│   ├── webm_clip.py        # WebM 解码与播放
│   └── window.py           # 窗口、动画状态机与交互
├── assets/characters/      # 内置角色素材
├── packaging/              # PyInstaller 入口
├── tests/                  # 单元测试与 GUI 冒烟测试
└── requirements.txt
```

## 验证

```sh
python -m pytest -q
QT_QPA_PLATFORM=xcb python tests/smoke.py
sh -n install-kde.sh
```

当前验证环境：Alpine Edge、KDE Plasma 6.7.4、KWin Wayland、XWayland、PySide6 6.11.1。

## 许可与致谢

- 本项目 Python 代码采用 MIT 许可。
- 动画素材及原始行为设计来自 [PC2005-cloud/dsh-pet](https://github.com/PC2005-cloud/dsh-pet)，其版权与许可归原项目所有。
- Windows/macOS Release 由 [MerZlin/dsh-pet-indesktop](https://github.com/MerZlin/dsh-pet-indesktop/releases) 提供。
- 社区优化实现：[ianlike-ui/dsh-pet-standalone](https://github.com/ianlike-ui/dsh-pet-standalone)。
