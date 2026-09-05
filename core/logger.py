# -*- coding: utf-8 -*-
"""日志:同时输出到 悬浮窗(队列) 与 日志文件(超过大小自动轮转归档)。"""
import datetime
import os
import threading


class Logger:
    def __init__(self, log_path: str | None = None, sink=None, max_mb: int = 5):
        self.sink = sink          # 悬浮窗队列 queue.Queue,元素为 (level, line)
        self.log_path = log_path
        self.max_mb = max_mb
        self._lock = threading.Lock()
        if log_path:
            try:
                if os.path.exists(log_path) and os.path.getsize(log_path) > max_mb * 1024 * 1024:
                    os.replace(log_path, log_path + ".old")
                    open(log_path, "a", encoding="utf-8").close()   # 轮转后立即建新日志文件
            except Exception:
                pass

    def _emit(self, level: str, msg: str) -> None:
        ts = datetime.datetime.now().strftime("%H:%M:%S")
        line = f"[{ts}] [{level}] {msg}"
        if self.sink is not None:
            try:
                self.sink.put((level, line))
            except Exception:
                pass
        if self.log_path:
            try:
                with self._lock:
                    with open(self.log_path, "a", encoding="utf-8") as f:
                        f.write(line + "\n")
            except Exception:
                pass

    def info(self, m: str) -> None:
        self._emit("INFO", m)

    def ok(self, m: str) -> None:
        self._emit("OK", m)

    def warn(self, m: str) -> None:
        self._emit("WARN", m)

    def error(self, m: str) -> None:
        self._emit("ERROR", m)

    def progress(self, m: str) -> None:
        self._emit("PROGRESS", m)
