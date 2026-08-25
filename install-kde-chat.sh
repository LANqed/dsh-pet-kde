#!/bin/sh
# dsh-pet for KDE Plasma —— Chat 版一键安装器（含 AI 对话）。
#
# 与 install-kde.sh 的唯一区别是默认安装 Chat 版：
# 会额外安装 pet/chat.py、pet/chat_ui.py、pet/chat_controller.py，
# 菜单里出现「和它说话…」与托盘「AI 对话」。
#
# 远程安装：
#   curl -fsSL .../install-kde-chat.sh | bash
# 本地安装（仓库根目录）：
#   ./install-kde-chat.sh
#
# 卸载与无 Chat 版完全一致：
#   ./install-kde-chat.sh --uninstall [--purge]
set -eu

DSH_PET_EDITION=chat
export DSH_PET_EDITION

REPO_URL=${DSH_PET_REPO_URL:-"https://github.com/LANqed/dsh-pet-kde"}
REPO_REF=${DSH_PET_REPO_REF:-main}

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" 2>/dev/null && pwd || true)
BASE_INSTALLER="$SCRIPT_DIR/install-kde.sh"

if [ -f "$BASE_INSTALLER" ]; then
    exec sh "$BASE_INSTALLER" "$@"
fi

# 通过 curl | bash 运行时本地没有脚本，拉取主安装器再执行
if command -v curl >/dev/null 2>&1; then
    FETCH="curl -fsSL"
elif command -v wget >/dev/null 2>&1; then
    FETCH="wget -qO-"
else
    printf '%s\n' "安装失败：需要 curl 或 wget。"
    exit 1
fi

RAW_BASE=$(printf '%s' "$REPO_URL" | sed 's#https://github.com/#https://raw.githubusercontent.com/#')
$FETCH "$RAW_BASE/$REPO_REF/install-kde.sh" | sh -s -- "$@"
