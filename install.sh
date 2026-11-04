#!/usr/bin/env bash
# install.sh — 安装 nautilus-zoxide-jump（幂等，可重复执行）
# usage: ./install.sh [-h|--help] [--no-restart]
set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
EXT_DST="$HOME/.local/share/nautilus-python/extensions"
BIN_DST="$HOME/bin/shortcuts"
RESTART=1

case "${1:-}" in
  -h|--help)
    sed -n '1,4p' "$0" | sed 's/^# \{0,1\}//'
    echo "  --no-restart   不执行 nautilus -q（稍后手动重启生效）"
    exit 0
    ;;
  --no-restart) RESTART=0 ;;
  '') ;;
  *) echo "install.sh: unknown arg: $1 (see -h)" >&2; exit 2 ;;
esac

[[ -f "$SRC/zoxide_jump.py" && -f "$SRC/nautilus-zoxide-toggle" ]] \
  || { echo "install.sh: missing files in $SRC" >&2; exit 1; }

# 依赖自检（扩展本体需要 python3-nautilus 才会被加载）
if ! dpkg -s python3-nautilus >/dev/null 2>&1; then
  echo "install.sh: python3-nautilus 未安装, 扩展将不会生效" >&2
  echo "  安装: pkexec apt install python3-nautilus" >&2
fi

mkdir -p "$EXT_DST" "$BIN_DST"
install -m 644 "$SRC/zoxide_jump.py" "$EXT_DST/zoxide_jump.py"
install -m 755 "$SRC/nautilus-zoxide-toggle" "$BIN_DST/nautilus-zoxide-toggle"
rm -rf "$EXT_DST/__pycache__"

if [[ "$RESTART" == 1 ]]; then
  nautilus -q 2>/dev/null || true
  echo "installed: 扩展已就位, nautilus 已重启信号(-q), 下次打开自动加载"
else
  echo "installed: 扩展已就位; 需手动 nautilus -q 后重开生效"
fi
echo "脚本: $BIN_DST/nautilus-zoxide-toggle"
echo "键位: 把仓库里 niri-keybind.snippet.kdl 那行并入 keybinds.kdl 即可用 Mod+Z"
