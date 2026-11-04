#!/usr/bin/env bash
# uninstall.sh — 完全移除 nautilus-zoxide-jump（扩展与脚本均在用户空间, 无残留）
# usage: ./uninstall.sh [-h|--help]
set -euo pipefail

case "${1:-}" in
  -h|--help)
    sed -n '1,3p' "$0" | sed 's/^# \{0,1\}//'
    exit 0
    ;;
esac

rm -fv "$HOME/.local/share/nautilus-python/extensions/zoxide_jump.py"
rm -rfv "$HOME/.local/share/nautilus-python/extensions/__pycache__" 2>/dev/null || true
rm -fv "$HOME/bin/shortcuts/nautilus-zoxide-toggle"

nautilus -q 2>/dev/null || true
echo "removed. 若并入过 niri 键位, 请自行删除 keybinds.kdl 中的 Mod+Z 行。"
