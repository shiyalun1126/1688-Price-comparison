"""轻量配置加载：读取 .env 文件并写入环境变量（无第三方依赖）。

用法：在 app 启动时调用 load_dotenv()（幂等，可重复调用）。
"""
from __future__ import annotations

import os
from pathlib import Path


def load_dotenv(path: str | Path | None = None) -> None:
    """解析 .env（简单 key=value / key=value 带引号），缺失则忽略。"""
    env_path = Path(path) if path else (Path(__file__).resolve().parent.parent / ".env")
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip()
        # 去外层引号
        if len(val) >= 2 and val[0] == val[-1] and val[0] in ("'", '"'):
            val = val[1:-1]
        # 不覆盖已存在的环境变量（命令行优先）
        if key and key not in os.environ:
            os.environ[key] = val


# 导入即加载一次，便于直接运行脚本
load_dotenv()