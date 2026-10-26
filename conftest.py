"""工程根 conftest：把工程根与 M2 源码目录加入 ``sys.path``。

- 工程根：``topology`` / ``brains`` 包；
- ``control-plane/src``：M2 顶层模块（``service`` / ``router`` / ``profile`` /
  ``evidence`` / ``merge`` / ``audit`` / ``api``）。该目录名含连字符，
  故通过把 ``src`` 目录直接挂到 ``sys.path`` 暴露其顶层模块。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SRC = ROOT / "control-plane" / "src"

for _path in (ROOT, SRC):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))