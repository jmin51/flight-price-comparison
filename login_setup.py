"""一次性登录各平台，把登录态存进持久化浏览器目录。

双击「登录各大平台.bat」运行。打开的是有界面的真浏览器，你用手机号+验证码登录，
看到已登录状态后在终端按回车，进入下一家。全部完成后登录态会长期保存在
data/browser_profile/，后续 --verify 核价直接复用，不用再登。

不登录也能核价（游客公开价），只是看不到会员价。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from flightwatch.config import load  # noqa: E402
from flightwatch.verify import PLATFORMS  # noqa: E402


def main() -> int:
    cfg = load()
    profile = Path(cfg.root) / "data" / "browser_profile"
    profile.mkdir(parents=True, exist_ok=True)

    targets = list((cfg.raw.get("verify") or {}).get("platforms") or ["trip", "ly", "fliggy"])

    print("=" * 62)
    print("  平台登录（登录态保存到 data/browser_profile/）")
    print("=" * 62)
    print(f"\n将要登录：{'、'.join(PLATFORMS.get(k, {}).get('name', k) for k in targets)}")
    print("提示：登录成功后不用关浏览器，直接在这里按回车即可。")
    print("如果某家不想登录，直接按回车跳过。\n")

    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            user_data_dir=str(profile),
            headless=False,
            viewport={"width": 1280, "height": 860},
            locale="zh-CN",
            timezone_id="Asia/Shanghai",
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
            ),
            args=["--disable-blink-features=AutomationControlled", "--no-first-run"],
        )
        ctx.add_init_script(
            "Object.defineProperty(navigator,'webdriver',{get:()=>undefined});"
        )
        try:
            for key in targets:
                meta = PLATFORMS.get(key, {"name": key, "login": ""})
                if not meta.get("login"):
                    continue
                page = ctx.new_page()
                print(f"\n>>> {meta['name']}：{meta['login']}")
                try:
                    page.goto(meta["login"], wait_until="domcontentloaded", timeout=45000)
                except Exception as e:  # noqa: BLE001
                    print(f"    页面打开失败：{type(e).__name__}: {e}")
                print(f"    请在浏览器窗口里完成登录，然后回到这里按回车…")
                try:
                    input()
                except (EOFError, KeyboardInterrupt):
                    pass
                page.close()
                print(f"    ✅ {meta['name']} 已保存")
        finally:
            ctx.close()

    print("\n" + "=" * 62)
    print("  全部完成。登录态已写入 data/browser_profile/")
    print("  之后双击「一键扫描.bat」或加 --verify 会自动复用。")
    print("=" * 62)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
