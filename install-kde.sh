#!/bin/sh
set -eu

APP_ID="com.merzlin.dsh-pet-standalone"
REPO_URL=${DSH_PET_REPO_URL:-"https://github.com/LANqed/dsh-pet-kde"}
REPO_REF=${DSH_PET_REPO_REF:-main}
UPSTREAM_ASSETS_URL=${DSH_PET_ASSETS_URL:-"https://github.com/PC2005-cloud/dsh-pet/archive/refs/heads/main.tar.gz"}

say() {
    printf '%s\n' "$*"
}

download_file() {
    url=$1
    output=$2
    if command -v curl >/dev/null 2>&1; then
        curl -fL --retry 3 --connect-timeout 15 "$url" -o "$output"
    elif command -v wget >/dev/null 2>&1; then
        wget -O "$output" "$url"
    else
        say "安装失败：需要 curl 或 wget。"
        exit 1
    fi
}

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" 2>/dev/null && pwd || true)
BOOTSTRAP_DIR=
if [ "${1:-}" != "--uninstall" ] && [ "${1:-}" != "--help" ] \
    && [ "${1:-}" != "-h" ] \
    && { [ ! -d "$SCRIPT_DIR/pet" ] || [ ! -f "$SCRIPT_DIR/requirements.txt" ]; }; then
    BOOTSTRAP_DIR=$(mktemp -d)
    trap 'rm -rf "$BOOTSTRAP_DIR"' EXIT
    ARCHIVE="$BOOTSTRAP_DIR/project.tar.gz"
    say "正在下载 dsh-pet 源码和安装文件..."
    download_file "$REPO_URL/archive/refs/heads/$REPO_REF.tar.gz" "$ARCHIVE"
    tar -xzf "$ARCHIVE" -C "$BOOTSTRAP_DIR"
    SCRIPT_DIR=$(find "$BOOTSTRAP_DIR" -mindepth 1 -maxdepth 2 -type d -name pet -print -quit | sed 's#/pet$##')
    if [ -z "$SCRIPT_DIR" ] || [ ! -f "$SCRIPT_DIR/requirements.txt" ]; then
        say "安装失败：远程源码归档结构无效。"
        exit 1
    fi
fi
DATA_HOME=${XDG_DATA_HOME:-"$HOME/.local/share"}
CONFIG_HOME=${XDG_CONFIG_HOME:-"$HOME/.config"}
INSTALL_DIR="$DATA_HOME/dsh-pet"
BIN_DIR="$HOME/.local/bin"
LAUNCHER="$BIN_DIR/dsh-pet"
DESKTOP_FILE="$DATA_HOME/applications/$APP_ID.desktop"
AUTOSTART_FILE="$CONFIG_HOME/autostart/$APP_ID.desktop"
PROFILE_FILE="$HOME/.profile"

running_pids() {
    uid=$(id -u)
    for proc in /proc/[0-9]*; do
        [ -r "$proc/status" ] && [ -r "$proc/comm" ] && [ -r "$proc/cmdline" ] || continue
        proc_uid=$(awk '/^Uid:/{print $2; exit}' "$proc/status")
        [ "$proc_uid" = "$uid" ] || continue
        IFS= read -r comm < "$proc/comm" || continue
        case "$comm" in
            python*) ;;
            *) continue ;;
        esac
        if tr '\000' '\n' < "$proc/cmdline" \
            | awk 'previous == "-m" && $0 == "pet" { found = 1 } { previous = $0 } END { exit !found }'; then
            pid=${proc##*/}
            [ "$pid" = "$$" ] || printf '%s\n' "$pid"
        fi
    done
}

has_system_runtime() {
    command -v python3 >/dev/null 2>&1 \
        && command -v ffmpeg >/dev/null 2>&1 \
        && command -v Xwayland >/dev/null 2>&1
}

run_root() {
    if [ "$(id -u)" -eq 0 ]; then
        "$@"
    elif command -v sudo >/dev/null 2>&1; then
        sudo "$@"
    else
        say "需要管理员权限安装系统依赖，但系统没有 sudo：$*"
        exit 1
    fi
}

uninstall() {
    # Disable autostart before checking the process. If the app is still open,
    # leave the installation intact so its running files cannot be deleted.
    rm -f "$AUTOSTART_FILE"
    pids=$(running_pids || true)
    if [ -n "$pids" ]; then
        say "卸载已中止：检测到 dsh-pet 仍在运行（PID: $(printf '%s' "$pids" | tr '\n' ' ')）。"
        say "请先从托盘退出 dsh-pet，然后重新执行卸载命令。"
        exit 1
    fi
    rm -rf "$INSTALL_DIR"
    rm -f "$LAUNCHER" "$DESKTOP_FILE"
    if [ "${1:-}" = "--purge" ]; then
        rm -rf "$CONFIG_HOME/dsh-pet-standalone"
    fi
    command -v kbuildsycoca6 >/dev/null 2>&1 && kbuildsycoca6 >/dev/null 2>&1 || true
    say "dsh-pet 已卸载。"
}

case "${1:-}" in
    --uninstall)
        uninstall "${2:-}"
        exit 0
        ;;
    --help|-h)
        say "用法: ./install-kde.sh [--uninstall [--purge]] [--no-launch]"
        exit 0
        ;;
    ""|--no-launch)
        ;;
    *)
        say "未知参数: $1"
        exit 2
        ;;
esac

if [ ! -d "$SCRIPT_DIR/pet" ]; then
    say "安装失败：脚本必须放在项目根目录运行。"
    exit 1
fi

if [ ! -d "$SCRIPT_DIR/assets/characters" ] \
    || ! find "$SCRIPT_DIR/assets/characters" -type f -name '*.webm' -print -quit | grep -q .; then
    if [ -d "$SCRIPT_DIR/assets/thumb" ] \
        && find "$SCRIPT_DIR/assets/thumb" -type f -name '*.webm' -print -quit | grep -q .; then
        mkdir -p "$SCRIPT_DIR/assets/characters/deepseek-tan/videos"
        cp "$SCRIPT_DIR/assets/thumb"/*.webm "$SCRIPT_DIR/assets/characters/deepseek-tan/videos/"
    else
        BOOTSTRAP_DIR=${BOOTSTRAP_DIR:-$(mktemp -d)}
        trap 'rm -rf "$BOOTSTRAP_DIR"' EXIT
        ASSET_ARCHIVE="$BOOTSTRAP_DIR/upstream-assets.tar.gz"
        say "正在下载默认角色素材..."
        download_file "$UPSTREAM_ASSETS_URL" "$ASSET_ARCHIVE"
        tar -xzf "$ASSET_ARCHIVE" -C "$BOOTSTRAP_DIR"
        THUMB_DIR=$(find "$BOOTSTRAP_DIR" -type d -path '*/assets/thumb' -print -quit)
        if [ -z "$THUMB_DIR" ] || ! find "$THUMB_DIR" -type f -name '*.webm' -print -quit | grep -q .; then
            say "安装失败：远程素材归档中没有找到 assets/thumb/*.webm。"
            exit 1
        fi
        mkdir -p "$SCRIPT_DIR/assets/characters/deepseek-tan/videos"
        cp "$THUMB_DIR"/*.webm "$SCRIPT_DIR/assets/characters/deepseek-tan/videos/"
    fi
fi

say "正在安装 dsh-pet for KDE Plasma..."

IS_ALPINE=0
if [ -f /etc/alpine-release ]; then
    IS_ALPINE=1
    if ! has_system_runtime \
        || ! python3 -c 'import PySide6, imageio_ffmpeg' >/dev/null 2>&1; then
        run_root apk add python3 py3-pyside6 py3-imageio-ffmpeg ffmpeg xwayland
    fi
else
    if ! has_system_runtime; then
        if command -v apt-get >/dev/null 2>&1; then
            run_root apt-get update
            run_root apt-get install -y python3 python3-venv python3-pip ffmpeg xwayland
        elif command -v dnf >/dev/null 2>&1; then
            run_root dnf install -y python3 python3-pip ffmpeg xorg-x11-server-Xwayland
        elif command -v pacman >/dev/null 2>&1; then
            run_root pacman -S --needed --noconfirm python python-pip ffmpeg xorg-xwayland
        elif command -v zypper >/dev/null 2>&1; then
            run_root zypper --non-interactive install python3 python3-pip ffmpeg xwayland
        else
            say "安装失败：请先安装 Python 3.10+、ffmpeg 和 XWayland。"
            exit 1
        fi
    fi
fi

rm -rf "$INSTALL_DIR"
mkdir -p "$INSTALL_DIR/assets" "$BIN_DIR" "$DATA_HOME/applications"
cp -R "$SCRIPT_DIR/pet" "$INSTALL_DIR/pet"
cp -R "$SCRIPT_DIR/assets/characters" "$INSTALL_DIR/assets/characters"
cp "$SCRIPT_DIR/requirements.txt" "$INSTALL_DIR/requirements.txt"

if [ "$IS_ALPINE" -eq 1 ]; then
    PYTHON=$(command -v python3)
else
    if ! python3 -m venv "$INSTALL_DIR/.venv" 2>/dev/null; then
        if command -v apt-get >/dev/null 2>&1; then
            run_root apt-get update
            run_root apt-get install -y python3-venv python3-pip ffmpeg xwayland
            python3 -m venv "$INSTALL_DIR/.venv"
        else
            say "安装失败：Python venv 不可用，请安装对应发行版的 python3-venv。"
            exit 1
        fi
    fi
    "$INSTALL_DIR/.venv/bin/python" -m pip install --upgrade pip
    "$INSTALL_DIR/.venv/bin/python" -m pip install -r "$INSTALL_DIR/requirements.txt"
    PYTHON="$INSTALL_DIR/.venv/bin/python"
fi

cat > "$LAUNCHER" <<EOF
#!/bin/sh
cd "$INSTALL_DIR"
exec "$PYTHON" -m pet "\$@"
EOF
chmod +x "$LAUNCHER"

if ! grep -F '$HOME/.local/bin' "$PROFILE_FILE" >/dev/null 2>&1; then
    {
        printf '\n# Added by dsh-pet installer\n'
        printf 'export PATH="$HOME/.local/bin:$PATH"\n'
    } >> "$PROFILE_FILE"
fi

cat > "$DESKTOP_FILE" <<EOF
[Desktop Entry]
Type=Application
Name=dsh-pet
GenericName=Desktop Pet
Comment=Desktop pet for KDE Plasma
Exec=$LAUNCHER
Icon=applications-games
Terminal=false
Categories=Game;Utility;
StartupNotify=false
EOF

command -v kbuildsycoca6 >/dev/null 2>&1 && kbuildsycoca6 >/dev/null 2>&1 || true

say "安装完成。"
say "启动命令: $LAUNCHER"
say "也可以在 KDE 应用菜单中搜索 dsh-pet。"
case ":${PATH}:" in
    *":$BIN_DIR:"*) ;;
    *) say "dsh-pet 命令已注册；重新打开终端后生效。当前终端可执行：export PATH=\"$BIN_DIR:\$PATH\"" ;;
esac
say "卸载命令: $SCRIPT_DIR/install-kde.sh --uninstall"

if [ "${1:-}" != "--no-launch" ]; then
    "$LAUNCHER" >/dev/null 2>&1 &
    say "dsh-pet 已启动。"
fi
