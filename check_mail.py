"""邮件通道一键诊断。

双击「检测邮件配置.bat」运行。按 config.yaml 的 mail.channel 自动选择检测路径：
  - resend：校验 API Key 是否有效（不发送邮件）
  - smtp  ：检测端口封锁 + 登录验证（不发送邮件）

加 --send 参数会真的发一封测试邮件。
"""
from __future__ import annotations

import json
import smtplib
import socket
import ssl
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from flightwatch.config import load  # noqa: E402
from flightwatch import notify  # noqa: E402

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)


def line(t=""):
    print(t)


def is_fake_ip(ip: str) -> bool:
    """Clash / sing-box 的 fake-ip 段。"""
    return ip.startswith("198.18.") or ip.startswith("198.19.") or ip.startswith("fdfe:")


def check_resend(cfg, send_test: bool) -> int:
    m = cfg.mail
    key = (m.get("resend_api_key") or "").strip()
    to = list(m.get("to") or [])

    line("\n[2] Resend API Key 校验")
    line(f"    Key 前缀 : {key[:12]}…（长度 {len(key)}）")
    line(f"    发件人   : {m.get('resend_from') or 'onboarding@resend.dev'}")
    line(f"    收件人   : {', '.join(to) or '❌ 未配置'}")
    if not key:
        line("    ❌ 未配置 resend_api_key")
        return 1
    if not to:
        line("    ❌ 未配置 mail.to")
        return 1

    # GET /domains 是最轻量的鉴权检查，不消耗发信额度
    req = urllib.request.Request(
        "https://api.resend.com/domains",
        headers={"Authorization": f"Bearer {key}", "Accept": "application/json", "User-Agent": UA},
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            data = json.loads(r.read().decode("utf-8", "ignore") or "{}")
        items = data.get("data") or []
        line(f"    ✅ Key 有效（已注册域名 {len(items)} 个）")
        if not items:
            line("    ⚠️  未验证自有域名 → 发件人只能用 onboarding@resend.dev，")
            line("       且收件人只能是注册邮箱本身。对当前用法没有任何影响。")
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "ignore")[:200]
        if e.code in (401, 403) and "restricted_api_key" in body:
            # 新建 Key 默认是「只能发信」权限，读 /domains 会被拒，这是正常的
            line("    ✅ Key 有效（权限为「仅发送 emails」，读取类接口被拒属正常）")
        elif e.code in (401, 403):
            line(f"    ❌ Key 无效或无权限（HTTP {e.code}）: {body}")
            line("    → 重新复制 API Keys 页面里的完整 Key（re_ 开头）")
            return 2
        else:
            line(f"    ❌ HTTP {e.code}: {body}")
            return 2
    except Exception as e:
        line(f"    ❌ {type(e).__name__}: {e}")
        return 3

    if send_test:
        line("\n[3] 实际发送测试邮件")
        ok, msg = notify.send(cfg, "【测试】机票监控邮件通道", "<p>收到这封说明邮件通道已通，降价提醒会发到这个地址。</p>")
        line(f"    {'✅' if ok else '❌'} {msg}")

    line("\n" + "=" * 62)
    if send_test:
        line("  结论：Resend 通道正常（已发送测试邮件，请查收，含垃圾箱）。")
    else:
        line("  结论：Resend 通道正常（Key 有效）。加 --send 参数可实测发信。")
    line("=" * 62)
    return 0


def check_smtp(cfg, send_test: bool) -> int:
    m = cfg.mail
    host = m.get("smtp_host", "smtp.qq.com")
    port = int(m.get("smtp_port", 465))
    pwd = m.get("password") or ""

    line("\n[2] SMTP 配置")
    line(f"    服务器   : {host}:{port} (SSL={m.get('use_ssl', True)})")
    line(f"    授权码   : {'已填写，长度 ' + str(len(pwd)) if pwd else '❌ 未填写'}")
    if not pwd:
        line("    → 请到 config.yaml 的 mail.password 填入 QQ 邮箱授权码")
        return 1

    line("\n[3] DNS 解析")
    try:
        infos = socket.getaddrinfo(host, port, socket.AF_INET)
        ips = sorted({i[4][0] for i in infos})
        line(f"    {host} -> {', '.join(ips)}")
        if any(is_fake_ip(ip) for ip in ips):
            line("    ⚠️  解析到 fake-IP 段，说明梯子接管了 DNS")
            line("       SMTP 流量会被送进代理节点，而多数节点封了邮件端口")
    except Exception as e:
        line(f"    ❌ 解析失败: {e}")
        return 1

    line("\n[4] SMTP 端口真实性检测")
    line("    （fake-IP 模式下 TCP 连接是本地伪造的，必须做 TLS 握手或读 banner 才算数）")
    any_open = False
    for p, mode in ((465, "tls"), (587, "banner"), (25, "banner")):
        try:
            raw = socket.create_connection((host, p), timeout=8)
            raw.settimeout(8)
            try:
                if mode == "tls":
                    ctx = ssl.create_default_context()
                    s = ctx.wrap_socket(raw, server_hostname=host)
                    line(f"    {p:>5}/tcp  ✅ TLS 握手成功 {s.version()}")
                    s.close()
                else:
                    banner = raw.recv(160).decode("utf-8", "ignore").strip()
                    line(f"    {p:>5}/tcp  ✅ 收到 banner: {banner[:48]}")
                any_open = True
            finally:
                try:
                    raw.close()
                except Exception:
                    pass
        except Exception as e:
            line(f"    {p:>5}/tcp  ❌ {type(e).__name__}（连上了但握手/读数据失败 = 被墙或节点封端口）")

    line("\n[5] 对照：HTTPS 443")
    try:
        raw = socket.create_connection(("mail.qq.com", 443), timeout=8)
        raw.settimeout(8)
        ctx = ssl.create_default_context()
        s = ctx.wrap_socket(raw, server_hostname="mail.qq.com")
        line(f"    mail.qq.com:443  ✅ TLS 握手成功 {s.version()}")
        s.close()
        line("    → 说明网络本身是通的，问题只出在 SMTP 端口")
    except Exception as e:
        line(f"    mail.qq.com:443  ❌ {type(e).__name__}")

    if not any_open:
        line("\n" + "=" * 62)
        line("  结论：SMTP 端口全被封，邮件发不出去")
        line("=" * 62)
        line("\n  授权码本身没问题，是梯子节点封了 465/587/25 出站。")
        line("  解法（推荐第二种，已经配好了）：")
        line("")
        line("  【A】让 qq.com 走直连（免费）")
        line("       Clash / Clash Verge：模式切到「规则」，")
        line("       或加一条 DOMAIN-SUFFIX,qq.com,DIRECT")
        line("")
        line("  【B】改用 HTTPS 邮件通道")
        line("       config.yaml 里 mail.channel 改成 resend，")
        line("       填好 mail.resend_api_key，再跑本检测。")
        line("=" * 62)
        return 2

    line("\n[6] SMTP 登录验证（不发送邮件）")
    try:
        ctx = ssl.create_default_context()
        if m.get("use_ssl", True):
            srv = smtplib.SMTP_SSL(host, port, timeout=20, context=ctx)
        else:
            srv = smtplib.SMTP(host, port, timeout=20)
            srv.starttls(context=ctx)
        with srv:
            srv.login(m["username"], pwd)
        line("    ✅ 登录成功，授权码有效")
        if send_test:
            ok, msg = notify.send(cfg, "【测试】机票监控邮件通道", "<p>SMTP 通道测试。</p>")
            line(f"\n[7] 实际发送：{'✅' if ok else '❌'} {msg}")
        line("\n" + "=" * 62)
        line("  结论：邮件通道正常，可以正常发送。")
        line("=" * 62)
        return 0
    except smtplib.SMTPAuthenticationError as e:
        line(f"    ❌ 认证失败：授权码不对（不是 QQ 密码，是 16 位授权码）")
        line(f"       {e}")
        return 3
    except Exception as e:
        line(f"    ❌ {type(e).__name__}: {e}")
        line("    → 能连端口但握手失败，多半是梯子中途掐断，改用 resend 通道更省事")
        return 4


def main() -> int:
    cfg = load()
    m = cfg.mail
    send_test = "--send" in sys.argv
    channel = (m.get("channel") or "smtp").lower()

    line("=" * 62)
    line("  邮件通道诊断")
    line("=" * 62)

    line("\n[1] 当前配置")
    line(f"    通道     : {channel}")
    line(f"    开关     : {'开启' if m.get('enabled') else '❌ 关闭'}")
    line(f"    收件人   : {', '.join(m.get('to') or [])}")
    if not m.get("enabled"):
        line("    → config.yaml 里 mail.enabled 改成 true")
        return 1

    if channel == "resend":
        return check_resend(cfg, send_test)
    return check_smtp(cfg, send_test)


if __name__ == "__main__":
    raise SystemExit(main())
