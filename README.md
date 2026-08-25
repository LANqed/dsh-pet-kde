# dsh-pet-indesktop for KDE

基于 [PC2005-cloud/dsh-pet](https://github.com/PC2005-cloud/dsh-pet) 的动画素材与行为模型实现的独立桌面宠物。纯 Linux 实现，面向 KDE Plasma，使用 Python、PySide6 和透明 WebM。

原项目fork:[MerZlin/dsh-pet-indesktop Releases](https://github.com/MerZlin/dsh-pet-indesktop/releases)

> 目前只在 Alpine Edge、KDE Plasma 6.7.4、Wayland + XWayland 环境中实机验证过。

## 两个版本

| 版本 | 适合 | 说明 |
| --- | --- | --- |
| **Chat 版** | 想体验完整功能（含 AI 对话） | 桌宠 + AI 对话气泡，可接任意 OpenAI 兼容接口 |
| **无 Chat 版** | 只需要桌宠陪伴 | 不含对话模块，包体更小、启动更轻 |

两个版本共用同一份配置与角色素材，可以随时换装，位置和设置都会保留。

## 安装

无需克隆仓库，一行命令安装。

**无 Chat 版**（只要桌宠陪伴）：

```sh
curl -fsSL https://raw.githubusercontent.com/LANqed/dsh-pet-kde/main/install-kde.sh | bash
```

**Chat 版**（含 AI 对话）：

```sh
curl -fsSL https://raw.githubusercontent.com/LANqed/dsh-pet-kde/main/install-kde-chat.sh | bash
```

也可以用主安装器加参数装 Chat 版：

```sh
curl -fsSL https://raw.githubusercontent.com/LANqed/dsh-pet-kde/main/install-kde.sh | bash -s -- --with-chat
```

安装器会下载程序和默认角色素材，检查或安装 Python、PySide6、imageio-ffmpeg、ffmpeg 与 XWayland，然后创建：

- 程序目录：`${XDG_DATA_HOME:-~/.local/share}/dsh-pet`
- 启动命令：`~/.local/bin/dsh-pet`
- KDE 应用菜单项：`dsh-pet`
- 版本标记：`${XDG_DATA_HOME:-~/.local/share}/dsh-pet/EDITION`（`chat` 或 `lite`）

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

**切换版本**：直接用另一个安装器重跑即可，配置和角色素材不会丢。

卸载并保留配置：

```sh
curl -fsSL https://raw.githubusercontent.com/LANqed/dsh-pet-kde/main/install-kde.sh | bash -s -- --uninstall
```

完全卸载，包括配置、日志、对话设置和位置记录：

```sh
curl -fsSL https://raw.githubusercontent.com/LANqed/dsh-pet-kde/main/install-kde.sh | bash -s -- --uninstall --purge
```

卸载时会先移除 KDE/XDG 自启动项，再检查 dsh-pet 是否仍在运行。若程序尚未退出，安装文件不会删除；请从托盘退出后重新执行卸载命令。

安装器支持 Alpine、Debian/Ubuntu、Fedora、Arch 和 openSUSE 系列发行版。程序安装在用户目录；仅在缺少系统依赖时通过 `sudo` 调用包管理器。

Windows 与 macOS 请使用原项目发布的安装包：**[MerZlin/dsh-pet-indesktop Releases](https://github.com/MerZlin/dsh-pet-indesktop/releases)**

## 功能

- WebM 高清播放：运行时直接解码 640x360、24fps、VP9 + 8-bit alpha WebM，保留半透明边缘
- 动画链：每个动画播完按概率选下一个 —— 30% 待机、10% 转向、40% 随机动作、20% 移动，永不停止
- 屏幕漫游：朝当前朝向行走，先检查屏幕空间不走出屏幕；动画前后各 2s 准备/收尾，位置由代码驱动
- 左右朝向：转向动画播完翻转朝向，所有动画支持水平镜像
- 点击回应：待机状态点击随机播放当前角色配置的回应动画
- 点击 Q 弹：点击立即产生“变矮再复原”的挤压回弹；连续点击可重复触发
- 拖拽：按住移动超过 5px 判定为拖拽，播放悬空反馈动画跟手
- 拖动物理：可开关。松手会按甩动速度抛出，带重力、屏幕边界反弹与摩擦衰减；拖拽和飞行中有惯性倾斜
- 播放速率：右键或托盘菜单可调 1.0x ~ 2.0x
- 多形象支持：自动发现内置角色，并支持从外部目录添加自定义角色
- 角色热切换：右键桌宠或托盘菜单随时切换形象，无需重启；可直接打开角色文件夹并重新扫描
- 透明穿透：非 KDE 的窗口管理器下逐帧按角色 alpha 生成窗口 mask，透明区域鼠标穿透到下层窗口
- 锁定：从角色右键菜单锁定后整窗鼠标穿透，只能从托盘解锁
- 窗口：透明无边框、可切换置顶、4 档大小；不显示在 KDE 任务管理器 / 底部 dock
- 置顶自检：合成器重启、分辨率/DPI 变更、休眠唤醒后置顶可能被丢弃，每 30 秒自检并自动恢复；点击或拖拽结束会把桌宠带回最前
- 动作等待间隔：可设 0~8 秒，相邻的非待机/非转向动画之间插入等待，等待期间只播待机与转向
- 自言自语气泡：可开关的随机气泡，间隔与文本可自定义；气泡定位在角色可见形象正上方并水平居中
- 解码降级：ffmpeg 缺失或被安全软件隔离时不崩溃，显示占位画面并弹窗提示；启动时后台预解码全部首帧（并发 3）
- 系统托盘：显示/隐藏、自言自语、锁定、切换角色、拖动物理、播放速度、开机自启、退出
- 自动持久化：位置、朝向、缩放、置顶、移动开关、锁定、播放速率、拖动物理、动作等待间隔、自言自语设置、当前角色
- **AI 对话（仅 Chat 版）**：头顶气泡对话，跟随桌宠移动；接任意 OpenAI 兼容接口，支持人设提示、多轮上下文、温度与最大输出、SSL 校验开关与连通性测试

## 使用

- 启动：执行 `dsh-pet`，或在 KDE 应用菜单中搜索 `dsh-pet`
- 退出：右键桌宠选择“退出”，或通过系统托盘退出
- 点击：待机状态下点击角色，先切到点击回应动画再 Q 弹（不会残留上一动画的帧）
- 拖拽：按住角色移动超过 5px 后进入拖拽状态；开启拖动物理时甩出会抛飞、落地反弹
- 右键菜单：只有角色当前可见像素响应右键。可选择动画、切换角色、复位位置、调整大小、播放速度、动作等待间隔、拖动物理、锁定、置顶、开机自启
- 锁定：选择“锁定并穿透鼠标”后，整个窗口不再接收鼠标，并气泡提示解锁入口；通过托盘取消“锁定（鼠标穿透）”解锁
- 播放速度：右键或托盘菜单 →「播放速度」，1.0x ~ 2.0x 立即生效
- 动作等待间隔：右键菜单 →「动作等待间隔」，默认「连续播放」；设为大于 0 会放缓动作密度
- 自言自语：托盘 →「自言自语」，可开关、立即说一句、进设置改间隔与文本
- 对话（Chat 版）：右键桌宠 →「和它说话…」，或托盘 →「AI 对话 → 说句话…」。Enter 发送，Esc 关闭输入框

## AI 对话（Chat 版）

首次使用需要填接口：托盘 →「AI 对话 → 设置…」。

配置写在 `${XDG_CONFIG_HOME:-~/.config}/dsh-pet-standalone/chat.json`（含 API key，权限 `600`）：

```json
{
  "enabled": true,
  "base_url": "https://api.deepseek.com/v1",
  "model": "deepseek-chat",
  "api_key": "sk-...",
  "system_prompt": "你是一只住在用户桌面上的桌宠……",
  "max_history": 12,
  "timeout": 30,
  "temperature": 0.8,
  "max_tokens": 512,
  "verify_ssl": true
}
```

任何 OpenAI 兼容的 `/v1/chat/completions` 端点都可以用：

| 服务 | base_url | model | api_key |
| --- | --- | --- | --- |
| DeepSeek | `https://api.deepseek.com/v1` | `deepseek-chat` | 必填 |
| OpenAI | `https://api.openai.com/v1` | `gpt-4o-mini` | 必填 |
| Ollama（本地） | `http://localhost:11434/v1` | `qwen2.5` | 留空 |
| OpenRouter | `https://openrouter.ai/api/v1` | 任选 | 必填 |

也可以用环境变量覆盖，适合不想把 key 写进文件的情况：

```sh
DSH_PET_CHAT_API_KEY=sk-... DSH_PET_CHAT_BASE_URL=https://api.deepseek.com/v1 dsh-pet
```

说明：

- 设置里有「测试连接」，会真实发一条最小请求（含 TLS 校验，10 秒超时），结果直接显示在对话框内
- 请求走后台线程，不会卡住动画；等待期间气泡显示「让我想想…」
- `max_history` 控制携带的历史条数，设 0 则每次都是新对话
- 切换角色会清空对话历史，旧角色的消息不会串进新角色
- 人设优先级：设置里的自定义提示 > 角色 `manifest.json` 的 `chat.system_prompt` > 内置默认
- 指向 `localhost`/`127.0.0.1` 时自动绕过系统代理，避免本地模型被 `http_proxy` 拦截
- 本地网关 / 自签名证书 / 代理拦截导致 `CERTIFICATE_VERIFY_FAILED` 时，可勾选「跳过 SSL 证书验证」（仅建议在可信环境使用）
- 只用 Python 标准库 `urllib`，Chat 版不增加任何第三方依赖

### 常见错误码

| 状态码 | 含义 | 处理方式 |
| --- | --- | --- |
| 401 / 403 | API key 无效或无权限 | 检查 key 是否正确、是否过期、账号权限是否足够 |
| 402 | 账户余额不足 | 到服务商平台充值；说明网络与证书均正常 |
| 404 | 地址或模型不存在 | 检查 `base_url` 与 `model` |
| 429 | 限流或额度不足 | 稍等重试，或减少历史条数 |
| 5xx | 服务端故障 | 服务商临时问题，稍后重试 |
| 网络无法连接 / 超时 | 地址不可达 | 检查网络与代理，确认超时值足够 |
| 证书校验失败 | TLS 校验不通过 | 勾选「跳过 SSL 证书验证」 |

## 自言自语气泡

托盘 →「自言自语」：

- 「开启气泡」：按随机间隔说一句本地文本
- 「立即说一句」：马上说一句（也用于预览）
- 「设置…」：改随机间隔上下限与文本内容

文本每行一条，留空则使用内置文本：

```text
好女孩……
好模型……
欧鲸鲸……
```

气泡显示在角色当前可见形象包围盒的正上方并水平居中；屏幕上方空间不足时自动翻到下方并反转尾巴。气泡是独立的浮层窗口，不会改变桌宠的透明 mask，也不阻止桌宠移动。

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
${XDG_CONFIG_HOME:-~/.config}/dsh-pet-standalone/characters/<角色ID>/videos/
```

一键安装会预先创建该目录并写入 `README.txt` 说明。最快的方式是：

1. 右键桌宠或托盘 →「切换角色」→「打开角色文件夹…」
2. 把 `<角色ID>/videos/` 放进去
3. 回到「切换角色」→「重新扫描角色」，新角色立即出现在菜单中

外部角色与内置角色 ID 相同时，优先使用外部版本。

### manifest.json

当文件名无法准确表达分类时，可在角色目录或 `videos/` 中添加：

```json
{
  "idle": "待机.webm",
  "turn": "转身.webm",
  "moves": ["走路.webm"],
  "clicks": ["点击回应.webm"],
  "drag": "拖拽.webm",
  "chat": {
    "system_prompt": "你是一个温柔的桌面宠物……"
  }
}
```

`chat.system_prompt` 是 Chat 版专用的角色人设；设置里没有自定义提示时会用它。无 Chat 版忽略该字段。

## 从源码运行

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

源码运行前需要确保 `assets/characters/<角色ID>/videos/` 中存在 WebM。远程 KDE 安装器在归档不含素材时，会从上游 dsh-pet 自动下载默认素材。

## 开机自启

菜单中的“开机自启”直接写入 XDG autostart，勾选状态直接读取该文件，不与配置冗余：

```text
${XDG_CONFIG_HOME:-~/.config}/autostart/com.merzlin.dsh-pet-standalone.desktop
```

## 技术实现

- `PySide6`：窗口、绘制、托盘和事件循环
- `imageio-ffmpeg`：后台线程解码 VP9 alpha WebM 为 RGBA
- `QTimer`：按视频帧率消费队列并驱动角色位置
- `ARGB32_Premultiplied`：避免 XWayland 透明 backing store 残影
- 每次播放使用独立停止事件，防止旧解码线程干扰新动画
- 解码队列满时等待消费，不主动丢弃动画帧
- 播放速率通过缩放帧消费定时器间隔实现，不重新解码
- Q 弹与拖动物理共用一个 60fps 定时器，空闲时自动停止
- Q 弹以脚底中点为锚只压高度，不放大宽度：窗口与 mask 尺寸固定，横向放大会把角色边缘裁成透明边
- Q 弹期间窗口 mask 与压扁画面用同一几何同步重算，贴边的耳朵/头顶装饰不会被上一帧的 mask 裁掉
- 点击先切到点击回应动画再启动 Q 弹，压扁的是新动画画面而不是旧帧
- 首帧在后台线程只预解码为 `QImage`（`QPixmap` 只能在 GUI 线程构造），`jumpToFrame(0)` 用缓存零卡顿建图
- 抛飞按每步积分重力与空气阻力，撞到屏幕可用区边界按系数反弹，速度低于阈值即静止并保存位置
- 气泡定位以角色可见像素包围盒为锚（按帧缓存），而不是带透明留白的窗口矩形
- 置顶用 `_NET_WM_STATE_ABOVE` client message 补发；watchdog 只在能读到「已丢失」时重设，读不到状态直接停表避免空转
- libX11 调用全部显式声明 `argtypes`/`restype` 并安装错误处理器，查询无效窗口不会因 BadWindow 终止进程
- 对话气泡与输入框是独立的无边框 Tool 窗口，不干扰桌宠自身的透明与 mask 逻辑
- 对话请求在后台线程执行，用请求代次（generation）丢弃过期结果，避免旧回复覆盖新提问
- 连通性测试用 daemon 线程 + 信号回主线程，不用 `QThread`：反复点击不会因对象在运行中被销毁而崩溃

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
├── install-kde.sh          # 一键安装器（默认无 Chat 版，--with-chat 装 Chat 版）
├── install-kde-chat.sh     # Chat 版安装器（等价于 --with-chat）
├── pet/
│   ├── app.py              # 应用入口与系统托盘
│   ├── autostart.py        # XDG 开机自启
│   ├── catalog.py          # 角色发现、动画分类与常量
│   ├── config.py           # 配置持久化
│   ├── features.py         # 可选功能探测（Chat 版 / 无 Chat 版）
│   ├── kde.py              # Plasma Wayland/XWayland 兼容
│   ├── library.py          # WebM 素材库与首帧预热
│   ├── webm_clip.py        # WebM 解码、播放、ffmpeg 自检与占位画面
│   ├── window.py           # 窗口、动画状态机、Q 弹、拖动物理与置顶自检
│   ├── speech_bubble.py    # 通用气泡与自言自语调度
│   ├── speech_settings.py  # 自言自语设置对话框
│   ├── x11_hints.py        # X11 任务栏跳过与置顶状态查询
│   ├── chat.py             # 仅 Chat 版：OpenAI 兼容后端
│   ├── chat_ui.py          # 仅 Chat 版：输入框与设置对话框
│   └── chat_controller.py  # 仅 Chat 版：对话与桌宠的粘合层
├── assets/characters/      # 内置角色素材
├── tests/                  # 单元测试与 GUI 冒烟测试
└── requirements.txt
```

两个版本共用同一份代码。无 Chat 版由安装器不复制 `chat.py`、`chat_ui.py`、`chat_controller.py` 实现；`pet/features.py` 探测不到这些模块时自动隐藏所有对话入口。

## 验证

```sh
python -m pytest -q
QT_QPA_PLATFORM=xcb python tests/smoke.py
sh -n install-kde.sh
sh -n install-kde-chat.sh
```

`pytest` 不需要素材即可运行；`tests/smoke.py` 需要默认角色的 WebM，缺失时会跳过并提示。

当前验证环境：Alpine Edge、KDE Plasma 6.7.4、KWin Wayland、XWayland、PySide6 6.11.1。

## 许可与致谢

- 本项目 Python 代码采用 MIT 许可。
- 动画素材及原始行为设计来自 [PC2005-cloud/dsh-pet](https://github.com/PC2005-cloud/dsh-pet)，其版权与许可归原项目所有。
- Windows/macOS Release 由 [MerZlin/dsh-pet-indesktop](https://github.com/MerZlin/dsh-pet-indesktop/releases) 提供。
- 社区优化实现：[ianlike-ui/dsh-pet-standalone](https://github.com/ianlike-ui/dsh-pet-standalone)。
