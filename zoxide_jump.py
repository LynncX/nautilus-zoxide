#!/usr/bin/env python3
"""zoxide-jump — 让 nautilus 获得 z 式原地目录跳转的扩展。

created  : 2026-07 (pi-desktop session fc28cdef)
goal     : 外部(rofi / niri Mod+Z)选好 zoxide 目录后，当前 nautilus 窗口原地切换
           过去，不开新标签也不开新窗口 —— 把终端 z / yazi Z 的肌肉记忆带进 GUI。
           同时覆盖 nautilus 承载的 portal 文件选择器窗口(GNOME 50: 浏览器等
           触发的"选择文件/文件夹"弹窗就在 nautilus 进程内)，让它也能 z 式跳转。
mechanism: 持有 DBus 名 dev.lynncx.zoxidejump；JumpTo(path) 选出最近聚焦的
           目标窗口 —— 普通浏览窗口(NautilusWindow)用 active-slot 的 'location'
           属性(GFile)赋值触发原地导航；文件选择器窗口(GtkFileChooser 系)则
           深层定位 GtkFileChooserWidget 并调用 set_current_folder_file。
           HasWindows() 供外部脚本判断是否已有窗口；DebugList() 内省所有
           顶层窗口(排障用)。

usage:
  本文件由 nautilus 自动加载，安装位置:
      ~/.local/share/nautilus-python/extensions/zoxide_jump.py
  外部调用:
      gdbus call --session --dest dev.lynncx.zoxidejump \
        --object-path /dev/lynncx/zoxidejump \
        --method dev.lynncx.zoxidejump.JumpTo '"/some/dir"'
      gdbus call --session --dest dev.lynncx.zoxidejump \
        --object-path /dev/lynncx/zoxidejump \
        --method dev.lynncx.zoxidejump.HasWindows
      gdbus call --session --dest dev.lynncx.zoxidejump \
        --object-path /dev/lynncx/zoxidejump \
        --method dev.lynncx.zoxidejump.DebugList
  重载: `nautilus -q` 后重开生效；改过代码需删除 __pycache__。
  直接运行(python3 zoxide_jump.py)仅打印本帮助。
"""

import os
import weakref

import gi

gi.require_version('Nautilus', '4.1')
gi.require_version('Gtk', '4.0')
from gi.repository import Nautilus, Gtk, Gio, GLib, GObject

BUS_NAME = 'dev.lynncx.zoxidejump'
OBJ_PATH = '/dev/lynncx/zoxidejump'

XML = f"""<node>
  <interface name='{BUS_NAME}'>
    <method name='JumpTo'>
      <arg type='s' direction='in'/><arg type='s' direction='out'/>
    </method>
    <method name='HasWindows'>
      <arg type='b' direction='out'/>
    </method>
    <method name='DebugList'>
      <arg type='s' direction='out'/>
    </method>
  </interface>
</node>"""


def _gtype(w):
    try:
        return type(w).__gtype__.name
    except Exception:
        return type(w).__name__


def _is_target(w):
    """可跳转的顶层窗口：普通浏览窗口或文件选择器窗口。"""
    n = _gtype(w)
    return 'NautilusWindow' in n or 'FileChooser' in n


def _targets():
    out = []
    for w in Gtk.Window.list_toplevels():
        if _is_target(w):
            out.append(w)
    return out


def _descendants(root):
    """深挖后代部件；AdwDialogHost 要额外钻 visible-dialog（对话框不是普通 child）。"""
    stack = [root]
    # 关键：必须持有 wrapper 强引用。用 id() 去重时，遍历大量列表项(如
    # GtkGridView 的上百个 GtkListItemWidget)会频繁创建/销毁 wrapper，
    # 被销毁 wrapper 的 id 被 Python 复用给新节点，造成“已访问”假象，
    # 导致真实节点被跳过(实测 NautilusSidebar/NautilusWindowSlot 丢失)。
    seen = []
    while stack:
        w = stack.pop()
        if any(s is w for s in seen):
            continue
        seen.append(w)
        yield w
        # 权威遍历：GTK4 的 first_child/next_sibling 兄弟链不依赖任何自定义
        # 属性/vfunc，能覆盖 AdwWindow 内部结构、OverlaySplitView 双分支、
        # BreakpointBin 等一切复合容器。
        try:
            c = w.get_first_child()
            while c is not None:
                stack.append(c)
                c = c.get_next_sibling()
        except Exception:
            pass
        # 属性兜底：模板类可能只挂属性，方法/兄弟链都探一遍
        for pn in ('child', 'content', 'sidebar'):
            try:
                c = w.get_property(pn)
                if c is not None:
                    stack.append(c)
            except Exception:
                pass
        # libadwaita 1.7+ 对话框宿主在窗口级(visible-dialog / dialogs)
        # libadwaita 1.7+ 对话框宿主在窗口级(visible-dialog / dialogs)
        try:
            d = w.get_property('visible-dialog')
            if d is not None:
                stack.append(d)
        except Exception:
            pass
        try:
            mdl = w.get_property('dialogs')
            for i in range(mdl.get_n_items()):
                it = mdl.get_item(i)
                if it is not None:
                    stack.append(it)
        except Exception:
            pass


def _find_first(w, gtype_name):
    for x in _descendants(w):
        if _gtype(x) == gtype_name:
            return x
    return None


class _FocusCache:
    """跟踪最近获得焦点的目标窗口（弱引用，不阻止窗口销毁）。

    rofi 弹出时 nautilus 侧窗口失去 is_active，等 rofi 关闭回调 JumpTo 时
    靠这里的缓存找回用户原本操作的窗口(浏览窗口或选择器)。
    """

    def __init__(self):
        self._ref = None

    def hook(self, w):
        if getattr(w, '_zj_hooked', False):
            return
        w._zj_hooked = True
        w.connect('notify::is-active', self._on_active)

    def _on_active(self, w, pspec):
        if w.get_property('is-active'):
            self._ref = weakref.ref(w)

    def best(self):
        wins = _targets()
        for w in wins:
            self.hook(w)
        if not wins:
            return None
        for w in wins:
            if w.get_property('is-active'):
                return w
        if self._ref is not None:
            w = self._ref()
            if w is not None and w in wins:
                return w
        return wins[0]


FOCUS = _FocusCache()


def _jump_browse(w, gfile):
    slot = w.get_property('active-slot')
    if slot is None:
        return 'ERR: no active slot'
    slot.set_property('location', gfile)
    return f'OK: {w.get_title()}'


def _jump_chooser(w, gfile):
    # NautilusFileChooser(nautilus 50 portal 选择器)：官方通道是侧栏/工具栏的
    # window-slot 属性——模板里 NautilusSidebar/NautilusToolbar 的 window-slot
    # 都 bind 到 slot_container.child，即 NautilusWindowSlot 实例(源码实证:
    # nautilus_file_chooser.c constructed: adw_bin_set_child(slot_container, slot))。
    # 直接向该 slot 的 location 属性赋值触发原地导航(与浏览窗口同机制)。
    for helper_name in ('NautilusSidebar', 'NautilusToolbar'):
        helper = _find_first(w, helper_name)
        if helper is None:
            continue
        try:
            slot = helper.get_property('window-slot')
        except Exception:
            slot = None
        if slot is not None and any(p.name == 'location' for p in slot.list_properties()):
            try:
                slot.set_property('location', gfile)
                return f'OK chooser(slot-via-{helper_name}): {w.get_title()}'
            except Exception as e:
                return f'ERR slot-via-{helper_name}: {type(e).__name__}: {e}'
    slot = _find_first(w, 'NautilusWindowSlot')
    if slot is not None and any(p.name == 'location' for p in slot.list_properties()):
        slot.set_property('location', gfile)
        return f'OK chooser(slot): {w.get_title()}'
    cw = _find_first(w, 'GtkFileChooserWidget')
    if cw is not None:
        cw.set_current_folder_file(gfile)
        return f'OK chooser(widget): {w.get_title()}'
    return 'ERR: no slot/chooser-widget found inside'


def _jump(path):
    w = FOCUS.best()
    if w is None:
        return 'ERR: no nautilus window'
    if not os.path.isdir(path):
        return f'ERR: not a directory: {path}'
    gfile = Gio.File.new_for_path(path)
    try:
        if _gtype(w) == 'NautilusWindow':
            return _jump_browse(w, gfile)
        return _jump_chooser(w, gfile)
    except Exception as e:
        return f'ERR: {type(e).__name__}: {e}'


def _debug_list():
    lines = [f'EXT_VERSION=v5-child-prop']
    for w in Gtk.Window.list_toplevels():
        n = _gtype(w)
        lines.append(f'== [{n}] title={w.get_title()!r} active={w.get_property("is-active")}')
        if n == 'NautilusWindow':
            try:
                slot = w.get_property('active-slot')
                lines.append(f'  slot.title={slot.get_property("title")!r}')
            except Exception as e:
                lines.append(f'  slot ERR: {type(e).__name__}')
            continue
        # 选择器探查：窗口属性 + 部件树(含 AdwDialog) + 命中能力
        try:
            lines.append(f'  window props: {[p.name for p in w.list_properties()]}')
        except Exception:
            pass
        try:
            m = [x for x in dir(w) if 'current_folder' in x or 'select_folder' in x]
            lines.append(f'  window chooser-methods: {m}')
            probes = {'slots': 0, 'chooser_widgets': 0}
            tree = []

            def emit(x, indent):
                n = _gtype(x)
                marks = []
                if n == 'NautilusWindowSlot':
                    probes['slots'] += 1
                    marks.append('HIT-SLOT')
                if n == 'GtkFileChooserWidget':
                    probes['chooser_widgets'] += 1
                    marks.append('HIT-CHOOSER')
                if n == 'NautilusSidebar' or n == 'NautilusToolbar':
                    try:
                        ws = x.get_property('window-slot')
                        marks.append(f'window-slot={None if ws is None else _gtype(ws)}')
                    except Exception as e:
                        marks.append(f'window-slot-ERR={type(e).__name__}')
                kids = []
                fc_err = ''
                try:
                    c = x.get_first_child()
                    while c is not None:
                        kids.append(c)
                        c = c.get_next_sibling()
                except Exception as e:
                    fc_err = f' FC-ERR {type(e).__name__}: {e}'
                suffix = (' [' + ' '.join(marks) + ']') if marks else ''
                tree.append(f'{indent}{n}{suffix}{fc_err} ({len(kids)} kids)')
                for k in kids:
                    emit(k, indent + '    ')

            emit(w, '    ')
            lines.append(f'  probes: {probes}')
            lines.extend(tree)
        except Exception as e:
            lines.append(f'  tree ERR: {type(e).__name__}: {e}')
    return '\n'.join(lines) or 'no toplevel windows'


class _Service:
    def __init__(self):
        Gio.bus_own_name(Gio.BusType.SESSION, BUS_NAME,
                         Gio.BusNameOwnerFlags.NONE, None,
                         self._acquired, None)

    def _acquired(self, conn, name):
        node = Gio.DBusNodeInfo.new_for_xml(XML)
        conn.register_object(OBJ_PATH, node.interfaces[0], self._dispatch)

    def _dispatch(self, conn, sender, path, iface, method, params, inv):
        try:
            if method == 'JumpTo':
                inv.return_value(GLib.Variant('(s)', (_jump(params[0]),)))
            elif method == 'HasWindows':
                inv.return_value(GLib.Variant('(b)', (bool(_targets()),)))
            elif method == 'DebugList':
                inv.return_value(GLib.Variant('(s)', (_debug_list(),)))
        except Exception as e:
            try:
                inv.return_value(GLib.Variant('(s)', (f'ERR {type(e).__name__}: {e}',)))
            except Exception:
                pass


class ZoxideJumpProvider(GObject.GObject, Nautilus.MenuProvider):
    """nautilus-python 的 loader 只加载含 Provider 子类的模块；菜单暂不使用。"""

    def __init__(self):
        super().__init__()
        self._svc = _Service()

    def get_background_items(self, folder):
        return []


if __name__ == '__main__':
    print(__doc__)
