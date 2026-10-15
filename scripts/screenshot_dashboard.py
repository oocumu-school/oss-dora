#!/usr/bin/env python3
"""
대시보드 자동 캡처 (GitHub Actions 에서 실행)
- 저장소 폴더를 로컬 웹서버로 띄우고, 헤드리스 브라우저로 dashboard/ 를 열어
  docs/dora/dashboard.png 로 저장한다. README 의 "3-2. 대시보드 화면"에 쓰인다.
- 필요: pip install playwright && python -m playwright install --with-deps chromium
"""
from __future__ import annotations

import functools
import http.server
import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "dora" / "dashboard.png"


def main() -> int:
    from playwright.sync_api import sync_playwright

    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(ROOT))
    handler.log_message = lambda *a, **k: None  # 접속 로그 숨김
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page(viewport={"width": 1280, "height": 900},
                                    color_scheme="light", locale="ko-KR", timezone_id="Asia/Seoul")
            page.goto(f"http://127.0.0.1:{port}/dashboard/", wait_until="networkidle")
            # 지표 카드가 그려지거나(정상) 오류 박스가 나올 때까지 대기
            page.wait_for_selector(".kpi, .error", timeout=20000)
            page.wait_for_timeout(1500)  # 차트 애니메이션이 끝날 때까지
            OUT.parent.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(OUT), full_page=True)
            browser.close()
    finally:
        server.shutdown()

    print(f"대시보드 캡처 저장: {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())