# nautilus-zoxide-jump

把终端 `z` / yazi `Z→fzf` 的肌肉记忆带进 nautilus：**Mod+Z → rofi 模糊搜索 zoxide 目录 → 当前窗口原地跳转**（不开新标签、不开新窗口）。无窗口时则在目标目录直接启动 nautilus。

## 原理

- `zoxide_jump.py`（nautilus python 扩展）：持有 DBus 名 `dev.lynncx.zoxidejump`，收到 `JumpTo(path)` 后找到最近聚焦的 `NautilusWindow`，对其 `active-slot` 激活 slot 自带的 `slot.open-location` GAction（URI 参数），走完整导航管线（重载视图+压前进/后退历史）。nautilus 50.2.2 实测有效
- `nautilus-zoxide-toggle`：`zoxide query -l` 喂给 rofi（fuzzy 匹配 + fzf 排序）；选中有窗口 → DBus 原地跳转 + niri 聚焦；无窗口 → 直接 `nautilus <dir>`
- 扩展与脚本全部安装到用户空间（`~/.local/share/nautilus-python/extensions/`、`~/bin/shortcuts/`），唯一系统级依赖是 apt 包 `python3-nautilus`（~300KB，`apt purge` 可完全清除）

## 安装

- 依赖：nautilus(GTK4)、python3-nautilus、zoxide、rofi；可选 jq+niri（聚焦）
- `./install.sh`
- 键位：把 `niri-keybind.snippet.kdl` 中的 `Mod+Z` 一行并入 `~/.config/niri/keybinds.kdl` 的 `binds` 块

## 使用

- `Mod+Z`：弹出 rofi，打几个字符（中英文皆可），回车跳转；Esc 取消
- 命令行直接调用：`nautilus-zoxide-toggle`（同上）
- 纯 DBus 调用：见 `zoxide_jump.py` 文件头 usage

## 验证 / 排障

- `gdbus call --session --dest dev.lynncx.zoxidejump --object-path /dev/lynncx/zoxidejump --method dev.lynncx.zoxidejump.HasWindows` 返回 `(true,)` 说明扩展已加载
- 改了扩展代码后不生效：`nautilus -q` 并删除 `~/.local/share/nautilus-python/extensions/__pycache__`
- 原地跳转失败退化为开新窗口时会有 notify-send 提示，多为扩展未加载（见上两条）
- 卸载：`./uninstall.sh`，再自行删除 keybinds.kdl 中的 `Mod+Z` 行

## 已知边界

- 多个 nautilus 窗口时，跳转目标是"最近聚焦的那个"
- 选择器弹窗（浏览器等应用经 portal 触发的"选择文件/文件夹"窗口，GNOME 50 由 nautilus 进程承载）：同样支持 z 式原地跳转——通过 NautilusToolbar/NautilusSidebar 的 window-slot 属性定位内嵌 slot 后导航
- 跳转列表按 zoxide frecency 排序；显示 `目录名␉路径`，名称与路径皆可模糊命中
- 选择器用 rofi(X11) 而非 fuzzel：fuzzel 未实现 Wayland text-input 协议，IME 无法工作（加环境变量也无效，它不是 GTK 程序）；rofi 走 XWayland+XIM→fcitx5，中文可用
- `python3-nautilus` 由系统 apt 管理，`pkexec apt purge python3-nautilus` 即完全清除

## 维护踩坑记录（改代码前必读）

导航机制（两条，均已实证）：
- 普通浏览窗口：对 `window.active-slot`（NautilusWindowSlot，注意它继承 **AdwBin**）激活 `slot.open-location` GAction——action group 由 nautilus_window_slot_init 以 "slot" 前缀插在 slot widget 上，参数为 URI 字符串；handler 调 `nautilus_window_slot_open_location_full` → `begin_location_change`，重载视图、压前进/后退历史、同步路径栏
- 陷阱：slot 的 `location` 属性 setter（`nautilus_window_slot_set_location`）只更新标题/历史按钮状态，**不重载视图**——曾导致"导航栏变了主体区不动"的 bug；且 open-location handler 对新旧地址相同会 early-return，绝不能先赋 `location` 属性再激活 action（那样一次导航都不会发生）
- portal 选择器（NautilusFileChooser，GNOME 50 起由 nautilus 进程承载浏览器等应用的"选择文件"弹窗）：内嵌的就是真 NautilusWindowSlot（`nautilus_file_chooser.c:743` `nautilus_window_slot_new`），slot 经 `NautilusSidebar` / `NautilusToolbar` 的 `window-slot` 属性可达（模板 bind 到 slot_container.child）；与浏览窗口共用同一 action 导航

遍历器（zoxide_jump.py 的 `_descendants`）三条铁律：
- 必须用 `get_first_child()`/`get_next_sibling()` 兄弟链遍历。`AdwWindow.get_child()` 返回的是内部 AdwDialogHost 空壳，真实内容挂在 `content` 属性；AdwOverlaySplitView 分 sidebar/content 两支；自定义模板类（NautilusShortcutManager 等）只注册属性、未必实现 get_child vfunc——方法、属性、兄弟链三种手段缺一不可，代码里三者都要保留
- 去重集合必须持有 wrapper 强引用（存对象本身，不是 id()）。用 id() 去重时，穿过 GtkGridView 的上百个列表项会频繁创建/销毁 PyGObject wrapper，被销毁 wrapper 的 id 被 Python 复用给新节点 → 真实节点被当"已访问"跳过。此 bug 曾连续数轮让 NautilusSidebar/NautilusWindowSlot 集体"消失"，切勿改回 id 去重
- 读属性可能抛异常（writable-only 属性如 window-slot 读不了），一律 try/except 兜底并记录异常类型

排障工作流：
- `DebugList` 输出第一行是 `EXT_VERSION`——排查前先确认运行中的守护进程加载的是哪版代码。部署静默失败会让调试变成对空气开枪（本项目真实发生过：py_compile 失败但命令链用 `;` 继续跑，浪费两轮）
- 部署三件套：`nautilus -q` → `pkill -x nautilus` → `rm -rf ~/.local/share/nautilus-python/extensions/__pycache__` → `diff` 校验部署文件与仓库一致
- portal 自测免打扰：`gdbus call --session --dest org.freedesktop.portal.Desktop --object-path /org/freedesktop/portal/desktop --method org.freedesktop.portal.FileChooser.OpenFile '' test '{"directory": <true>}'` 可自行唤起与浏览器完全一致的选择器窗口，不必每次手动到浏览器里触发
- nautilus 源码是最快的真相来源：从镜像 pool 直接下载 orig tar（如 `nautilus_50.2.2.orig.tar.xz`），重点读 `src/nautilus-file-chooser.c` 与 `src/resources/ui/nautilus-file-chooser.blp`；编译期 UI 字符串也可 `strings /usr/bin/nautilus` 与 `gresource list` 快速获取
- GType 名是精确匹配不是前缀：目标窗口过滤 `NautilusFileChooser` 时注意 NautilusWindowSlot 也含 "NautilusWindow" 字样，用 `in` 判断时先想清楚层级
