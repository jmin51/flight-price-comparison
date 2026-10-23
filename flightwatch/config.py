"""配置加载。"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent


@dataclass
class Config:
    raw: dict[str, Any]
    root: Path

    @property
    def route(self) -> dict[str, Any]:
        return self.raw.get("route", {})

    @property
    def window(self) -> dict[str, Any]:
        return self.raw.get("window", {})

    @property
    def scan(self) -> dict[str, Any]:
        return self.raw.get("scan", {})

    @property
    def network(self) -> dict[str, Any]:
        return self.raw.get("network", {})

    @property
    def alerts(self) -> dict[str, Any]:
        return self.raw.get("alerts", {})

    @property
    def mail(self) -> dict[str, Any]:
        return self.raw.get("mail", {})

    @property
    def report_cfg(self) -> dict[str, Any]:
        return self.raw.get("report", {})

    def db_path(self) -> Path:
        p = self.root / self.raw.get("database", "data/flights.db")
        p.parent.mkdir(parents=True, exist_ok=True)
        return p

    def report_dir(self) -> Path:
        p = self.root / self.report_cfg.get("dir", "reports")
        p.mkdir(parents=True, exist_ok=True)
        return p


def load(path: str | Path | None = None) -> Config:
    cfg_path = Path(path) if path else ROOT / "config.yaml"
    with open(cfg_path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}
    return Config(raw=raw, root=ROOT)


def _detect_system_proxy() -> str:
    """读取 Windows「系统代理」设置（Clash 开系统代理时写的就是这里）。

    探测不到就返回空串，调用方按直连处理。
    """
    if os.name != "nt":
        return ""
    try:
        import winreg

        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Internet Settings",
        )
        try:
            enabled = winreg.QueryValueEx(key, "ProxyEnable")[0]
        except FileNotFoundError:
            return ""
        if not enabled:
            return ""
        try:
            server = str(winreg.QueryValueEx(key, "ProxyServer")[0])
        except FileNotFoundError:
            return ""
    except Exception:  # noqa: BLE001
        return ""

    # ProxyServer 可能是 "127.0.0.1:6696" 或 "http=...;https=..."
    if "=" in server:
        parts = dict(
            p.split("=", 1) for p in server.split(";") if "=" in p
        )
        server = parts.get("https") or parts.get("http") or ""
    server = server.strip()
    if not server:
        return ""
    return server if server.startswith("http") else f"http://{server}"


def prepare_network(cfg: Config) -> None:
    """处理代理环境变量。

    WorkBuddy / 容器会把 HTTP_PROXY 注入成内部代理，fast-flights 读环境变量
    时会串过去导致连接失败，这里按配置清掉或改成指定代理。
    配置留空时自动探测 Windows 系统代理（Clash 开「系统代理」即可）。
    """
    if not cfg.network.get("strip_env_proxy", True):
        return

    proxy = (cfg.network.get("proxy") or "").strip()
    if not proxy:
        proxy = _detect_system_proxy()
        if proxy:
            print(f"[网络] 未配置代理，自动使用系统代理 {proxy}")
    keys = ["HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY", "all_proxy"]

    if proxy:
        for k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
            os.environ[k] = proxy
        for k in ("ALL_PROXY", "all_proxy"):
            os.environ.pop(k, None)
    else:
        for k in keys:
            os.environ.pop(k, None)
