"""TUI 语义色常量。

集中管理终端界面用到的颜色，避免散落的 ``color(80)`` / ``color(99)`` /
``color(242)`` 这类 ANSI 索引色。这些 ANSI 色是写死在 256 色调色板里的，
既不受 Theme 控制、也没有语义，换主题时不会跟着变。

这里定义一套「主题色板 + 消息语义色」，供两处使用：

- ``app.py`` 的 ``_KYNCODE_THEME`` 从这里取值，保证 CSS 主题一致；
- Python 侧的 ``RichText`` 渲染（rich 库，无法直接用 CSS 变量）也从这里
  取值，避免颜色在主题和渲染代码里各写一份、慢慢漂移。

配色气质：保留 kyncode 的紫色基因（primary 不变），把背景从纯黑
``#1a1a1a`` 换成三层递进的深蓝灰，让 surface/panel 与 background 拉开层次。
"""

# --- 主题色板 ---

PRIMARY = "#875FFF"  # kyncode 品牌紫，保持不变
SECONDARY = "#5A7FD6"  # 次色：柔和蓝
ACCENT = "#FFB86C"  # 强调/选中：柔和橙
WARNING = "#FFB86C"
ERROR = "#FF6B7A"
SUCCESS = "#6BD08A"
FOREGROUND = "#D8D8E0"  # 正文

# 三层背景，从深到浅递进，形成层次（原来是 #1a1a1a 纯黑无层次）
BACKGROUND = "#16161C"
SURFACE = "#1E1E26"
PANEL = "#2A2A34"

MUTED = "#6C7086"  # 静音文字（model/workspace、时间戳等）

# --- 消息前缀语义色 ---

USER_PREFIX = "#8AB4F8"  # 用户消息前缀 "❯"
AI_PREFIX = PRIMARY  # AI 消息前缀 "●"
