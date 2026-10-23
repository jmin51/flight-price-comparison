"""邮件推送（SMTP 直发，不依赖 WorkBuddy 在线）。"""
from __future__ import annotations

import smtplib
from email.header import Header
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr
from typing import Any

from .config import Config


def configured(cfg: Config) -> bool:
    m = cfg.mail
    if not m.get("enabled"):
        return False
    if (m.get("channel") or "smtp").lower() == "resend":
        return bool(m.get("resend_api_key") and m.get("to"))
    return bool(m.get("username") and m.get("password"))


def _wrap(title: str, inner: str) -> str:
    return f"""<!DOCTYPE html><html><head><meta charset="utf-8"></head>
<body style="margin:0;padding:24px;background:#f7f8fa;font-family:-apple-system,'Segoe UI',
'Microsoft YaHei',sans-serif;color:#1a1a1a;">
<div style="max-width:680px;margin:0 auto;background:#fff;border:1px solid #e5e7eb;
border-radius:12px;overflow:hidden;">
  <div style="padding:18px 22px;border-bottom:1px solid #eef0f2;">
    <div style="font-size:16px;font-weight:700;">{title}</div>
  </div>
  <div style="padding:20px 22px;font-size:13px;line-height:1.65;">{inner}</div>
  <div style="padding:14px 22px;background:#fafafa;color:#9ca3af;font-size:11px;
  border-top:1px solid #eef0f2;">数据源 Google Flights（fast-flights）· 往返含税总价</div>
</div></body></html>"""


def send(cfg: Config, subject: str, html_body: str) -> tuple[bool, str]:
    m = cfg.mail
    if not m.get("enabled"):
        return False, "邮件未启用"
    channel = (m.get("channel") or "smtp").lower()
    if channel == "resend":
        return _send_resend(cfg, subject, html_body)
    return _send_smtp(cfg, subject, html_body)


def _send_resend(cfg: Config, subject: str, html_body: str) -> tuple[bool, str]:
    """走 HTTPS 443 的邮件 API，不受梯子封 SMTP 端口影响。

    注册 resend.com 拿 API Key（免费 3000 封/月）。未验证自有域名时，
    发件人固定用 onboarding@resend.dev，且只能发给注册邮箱。
    """
    import json
    import urllib.error
    import urllib.request

    m = cfg.mail
    key = (m.get("resend_api_key") or "").strip()
    if not key:
        return False, "未配置 Resend API Key（config.yaml → mail.resend_api_key）"

    payload = {
        "from": m.get("resend_from") or "onboarding@resend.dev",
        "to": list(m.get("to") or []),
        "subject": subject,
        "html": html_body,
    }
    # 注意：必须带浏览器 UA，urllib 默认 UA 会被 Cloudflare 以 403 / error code 1010 拒绝
    req = urllib.request.Request(
        "https://api.resend.com/emails",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
            ),
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            body = r.read().decode("utf-8", "ignore")
        return True, f"已通过 Resend 发送 {body[:100]}"
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "ignore")[:200]
        return False, f"Resend HTTP {e.code}: {detail}"
    except Exception as e:
        return False, f"Resend {type(e).__name__}: {e}"


def _send_smtp(cfg: Config, subject: str, html_body: str) -> tuple[bool, str]:
    m = cfg.mail
    if not m.get("password"):
        return False, "未配置 SMTP 授权码（config.yaml → mail.password）"

    msg = MIMEMultipart("alternative")
    msg["Subject"] = Header(subject, "utf-8")
    msg["From"] = formataddr((str(Header(m.get("from_name", "机票监控"), "utf-8")), m["username"]))
    to_list: list[str] = list(m.get("to") or [m["username"]])
    msg["To"] = ", ".join(to_list)
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    host, port = m.get("smtp_host", "smtp.qq.com"), int(m.get("smtp_port", 465))
    try:
        if m.get("use_ssl", True):
            server: Any = smtplib.SMTP_SSL(host, port, timeout=30)
        else:
            server = smtplib.SMTP(host, port, timeout=30)
            server.starttls()
        with server:
            server.login(m["username"], m["password"])
            server.sendmail(m["username"], to_list, msg.as_string())
        return True, f"已发送至 {', '.join(to_list)}"
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"


def _verify_block(results: list[dict[str, Any]] | None, google_price: int | None) -> str:
    """邮件里的「真浏览器核价」小节。截图留在本地报告里，邮件只放数字。"""
    if not results:
        return ""
    rows = []
    for v in results:
        if v.get("price"):
            val = f'<b style="color:#0b6b3a;">¥{v["price"]:,}</b>'
            if google_price:
                d = v["price"] - google_price
                if d < 0:
                    val += f' <span style="color:#0b6b3a;font-size:11px;">低 ¥{-d:,}</span>'
                elif d > 0:
                    val += f' <span style="color:#9ca3af;font-size:11px;">高 ¥{d:,}</span>'
        else:
            val = f'<span style="color:#9ca3af;">{v.get("note") or "未取到"}</span>'
        rows.append(
            f'<tr><td style="padding:5px 8px;border-bottom:1px solid #f3f4f6;">'
            f'<a href="{v["url"]}" style="color:#2563eb;">{v["name"]}</a></td>'
            f'<td style="padding:5px 8px;border-bottom:1px solid #f3f4f6;">{val}</td></tr>'
        )
    return f"""
<div style="margin-top:20px;padding-top:14px;border-top:1px dashed #e5e7eb;">
  <div style="font-weight:600;margin-bottom:8px;">各平台实测核价（真浏览器）</div>
  <table style="width:100%;border-collapse:collapse;font-size:12px;">{''.join(rows)}</table>
  <div style="color:#9ca3af;font-size:11px;margin-top:8px;">各平台页面截图见本地报告。</div>
</div>"""


def build_alert_html(
    origin: str,
    dest: str,
    best: dict[str, Any],
    top: list[dict[str, Any]],
    reasons: list[str],
    report_name: str,
    verify_results: list[dict[str, Any]] | None = None,
) -> tuple[str, str]:
    """返回 (subject, html)。"""
    p = best["price"]
    subject = (
        f"【机票】{origin}→{dest} ¥{p:,} "
        f"| {best['dep_date'][5:]}→{best['ret_date'][5:]} {'·'.join(reasons)}"
    )

    rows = []
    for i, o in enumerate(top, 1):
        rows.append(
            f"""<tr>
              <td style="padding:7px 8px;border-bottom:1px solid #f3f4f6;color:#9ca3af">{i}</td>
              <td style="padding:7px 8px;border-bottom:1px solid #f3f4f6">{o['dep_date']}</td>
              <td style="padding:7px 8px;border-bottom:1px solid #f3f4f6">{o['ret_date']}</td>
              <td style="padding:7px 8px;border-bottom:1px solid #f3f4f6;text-align:right">
                  <b style="color:#0b6b3a">¥{o['price']:,}</b></td>
              <td style="padding:7px 8px;border-bottom:1px solid #f3f4f6;color:#6b7280">
                  {o.get('stops', '')} 转 · {o.get('gap_days', '')} 天</td>
            </tr>"""
        )

    inner = f"""
<p style="margin:0 0 14px;">触发原因：<b style="color:#0b6b3a">{'、'.join(reasons)}</b></p>
<div style="font-size:30px;font-weight:700;color:#0b6b3a;letter-spacing:-1px;margin:6px 0 2px;">
  ¥{p:,}</div>
<div style="color:#6b7280;margin-bottom:16px;">{best['dep_date']} → {best['ret_date']}　
往返含税 · {best.get('carriers','QR')} · {best.get('stops','')} 次中转</div>
<div style="color:#4b5563;font-size:12px;margin-bottom:18px;">{best.get('itinerary','')}</div>
<table style="width:100%;border-collapse:collapse;font-size:12px;">
<thead><tr>
  <th style="text-align:left;padding:7px 8px;color:#9ca3af;font-weight:600;border-bottom:1px solid #e5e7eb">#</th>
  <th style="text-align:left;padding:7px 8px;color:#9ca3af;font-weight:600;border-bottom:1px solid #e5e7eb">去程</th>
  <th style="text-align:left;padding:7px 8px;color:#9ca3af;font-weight:600;border-bottom:1px solid #e5e7eb">返程</th>
  <th style="text-align:right;padding:7px 8px;color:#9ca3af;font-weight:600;border-bottom:1px solid #e5e7eb">往返价</th>
  <th style="text-align:left;padding:7px 8px;color:#9ca3af;font-weight:600;border-bottom:1px solid #e5e7eb">条件</th>
</tr></thead><tbody>{''.join(rows)}</tbody></table>
<p style="margin:18px 0 0;">
  <a href="{best['deep_link']}" style="color:#2563eb;margin-right:16px;">Google Flights</a>
  <span style="color:#9ca3af;">本地报告：{report_name}</span>
</p>
{_verify_block(verify_results, best.get("price"))}
"""
    return subject, _wrap(f"{origin} → {dest}｜卡塔尔航空 · 多哈中转", inner)
