"""Real Chromium checks. Run after preview_agreements.py with uv --with playwright."""

import argparse
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import threading

from playwright.sync_api import sync_playwright, expect


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, default=Path("test_runs/agreements"))
    args = parser.parse_args()

    class Quiet(SimpleHTTPRequestHandler):
        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(
        ("127.0.0.1", 0), partial(Quiet, directory=str(args.directory))
    )
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_port}"
    checks = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        errors, requests = [], []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.on("request", lambda request: requests.append(request.url))
        page.goto(base + "/ran1/", wait_until="networkidle")
        assert not any("cdn.jsdelivr.net" in u or "/agreements/" in u for u in requests)
        checks.append("No CDN runtime or agreement body before selection")

        def close_popup(target=page):
            # The scroll from Playwright's click lands a frame later and may
            # already have closed the popup, so close it without waiting.
            target.evaluate("""() => document.querySelector('#popup-floating.show')
                && document.getElementById('popup-close-btn').click()""")

        def select(name):
            close_popup()
            page.get_by_role("button", name=name, exact=True).click()

        target = page.get_by_role(
            "button", name="Evaluation assumptions, AI 10.1", exact=True
        )
        target.scroll_into_view_if_needed()
        before = page.evaluate("window.scrollY")
        target.click()
        doc = page.locator(
            ".agreement-document"
        )  # Playwright traverses open Shadow DOM.
        expect(doc.locator("article")).to_have_count(1)
        expect(page.locator("#popup-floating")).to_have_class("popup-floating show")
        assert abs(page.evaluate("window.scrollY") - before) < 2
        assert page.locator("iframe").count() == 0
        assert page.get_by_text("Download original document").count() == 0
        assert page.get_by_text("Session details", exact=True).count() == 0
        assert len([u for u in requests if "/agreements/" in u]) == 1
        checks.append(
            "Original popup preserved; no automatic scroll, iframe or download controls"
        )
        expect(doc.locator("img")).to_have_count(3)
        assert doc.locator("img").evaluate_all(
            "(xs)=>xs.every(x=>x.complete && x.naturalWidth>0)"
        )
        assert doc.locator("math").count() > 0
        assert doc.evaluate(
            '(e)=>e.clientHeight === e.scrollHeight && getComputedStyle(e).maxHeight === "none"'
        )
        assert (
            doc.locator(".docx-document").evaluate("(e)=>getComputedStyle(e).fontSize")
            == "14px"
        )
        # Global p/td styles cannot enter the isolated Word document.
        assert (
            doc.locator("p").first.evaluate("(e)=>getComputedStyle(e).whiteSpace")
            == "pre-wrap"
        )
        checks.append(
            "Inline HTML, WMF and MathML with natural height and isolated CSS"
        )
        # node.style lists longhands, so text-decoration must survive as
        # text-decoration-line; struck-out text reverses an agreement's meaning.
        assert doc.locator("[style]").evaluate_all(
            "(xs)=>xs.filter(x=>x.style.textDecorationLine.includes('line-through')).length"
        ) == 6
        checks.append("Word strike-through survives style sanitization")
        for selector, marker in [
            ("ul", "disc"),
            ("ul ul", "circle"),
        ]:
            assert (
                doc.locator(selector).first.evaluate(
                    "(e)=>getComputedStyle(e).listStyleType"
                )
                == marker
            )
        assert doc.locator(".list-marker").count() == 0
        checks.append(
            "GFM-style nested list markers and spacing survive HTML sanitization"
        )
        for prefix in [
            "Regarding the gNB transmission power",
            "Note: The values defined in option1",
        ]:
            paragraph = doc.locator("p").filter(has_text=prefix).first
            assert paragraph.evaluate("(e)=>getComputedStyle(e).textIndent") == "0px"
            assert paragraph.evaluate("""e => {
                const range = document.createRange(); range.selectNodeContents(e);
                const boundary = e.closest('td') || e.getRootNode().host;
                return range.getBoundingClientRect().left >= boundary.getBoundingClientRect().left - 1;
            }""")
        checks.append(
            "Direct paragraph indent reset prevents text escaping body/table boundaries"
        )
        close_popup()
        doc.locator("article").filter(
            has_text="Regarding the gNB transmission power"
        ).screenshot(path=str(args.directory / "indent-fixed.png"))
        close_popup()
        page.locator(".agreement-header").evaluate(
            '(e)=>e.scrollIntoView({block:"start"})'
        )
        page.screenshot(path=str(args.directory / "desktop.png"))
        select("Energy efficiency, AI 10.4")
        expect(doc.locator("article")).to_have_count(1)
        expect(page.get_by_role("tab", name="AI 10.4", exact=True)).to_have_attribute(
            "aria-selected", "true"
        )
        expect(page.locator("#agreement-body h3")).to_have_text(
            "AI 10.4 Energy efficiency"
        )
        select("Evaluation assumptions, AI 10.1")
        expect(doc.locator("article")).to_have_count(1)
        assert len([u for u in requests if "/agreements/" in u]) == 2
        checks.append("Cell switching and successful HTML cache reuse")
        before_requests = len([u for u in requests if "/agreements/" in u])
        select("Multiple agendas, AI 10.5.1.1, 10.5.2.2")
        expect(page.get_by_role("tab")).to_have_count(2)
        expect(doc.locator("article")).not_to_have_count(0)
        assert len([u for u in requests if "/agreements/" in u]) == before_requests + 1
        close_popup()
        second = page.get_by_role("tab", name="AI 10.5.2.2", exact=True)
        second.click()
        expect(second).to_have_attribute("aria-selected", "true")
        expect(page.locator("#agreement-body h3")).to_contain_text("AI 10.5.2.2")
        expect(page.locator("#agreement-body")).to_have_attribute("aria-busy", "false")
        assert len([u for u in requests if "/agreements/" in u]) == before_requests + 2
        second.press("ArrowLeft")
        expect(page.get_by_role("tab", name="AI 10.5.1.1", exact=True)).to_be_focused()
        expect(page.locator(".agreement-document")).to_have_count(1)
        checks.append("Multiple AI tabs fetch only active AI; arrow-key navigation")
        select("Nested lists, AI 10.5.2.1")
        expect(doc.locator("ul ul").first).to_be_attached()
        expect(doc.locator("ul ul ul")).to_have_count(0)
        assert (
            doc.locator("ul ul").first.evaluate(
                "(e)=>getComputedStyle(e).listStyleType"
            )
            == "circle"
        )
        close_popup()
        doc.locator("article").first.screenshot(
            path=str(args.directory / "nested-lists.png")
        )
        select("Exact number missing, AI 10.10")
        expect(
            page.get_by_text(
                "AI 10.10 is not a heading in the chair notes."
            )
        ).to_be_visible()
        select("Section without Agreement label, AI 9.2.1")
        expect(doc.locator("article")).to_have_count(1)
        expect(doc).to_contain_text("Multiple frequency-domain starting positions")
        checks.append("Sections without an Agreement marker still show their source content")
        select("TDoc entries only, AI 9.7.1")
        expect(page.get_by_text("Only TDoc listings are recorded under this item.")).to_be_visible()
        select("Heading over subsections, AI 10.6.1")
        expect(page.get_by_role("tab")).to_have_count(3)
        expect(page.get_by_role("tab", name="AI 10.6.1.1")).to_have_attribute("aria-selected", "true")
        expect(doc).to_be_visible()
        page.get_by_role("tab", name="AI 10.6.1", exact=True).click()
        note = page.locator("#agreement-body .agreement-note")
        expect(note).to_have_text("Nothing is recorded directly under this item; see its subsections.")
        checks.append("A parent brings its subsections and opens on the first item with text")
        select("No agenda")
        expect(page.get_by_text("This cell has no agenda number.")).to_be_visible()
        expect(page.get_by_role("tab")).to_have_count(0)
        checks.append("Missing number and no-AI states")
        close_popup()
        target = page.get_by_role(
            "button", name="Energy efficiency, AI 10.4", exact=True
        )
        # Let the scroll that brings it into view land before the popup opens.
        target.scroll_into_view_if_needed()
        page.wait_for_timeout(100)
        target.focus()
        target.press("Enter")
        expect(target).to_have_attribute("aria-pressed", "true")
        expect(page.locator("#popup-floating")).to_have_class("popup-floating show")
        close_popup()
        target.focus()
        target.press("Space")
        expect(doc.locator("article")).to_have_count(1)
        close_popup()
        page.get_by_role("button", name="Tue", exact=True).click()
        expect(page.locator('.session-block[aria-pressed="true"]')).to_have_count(0)
        expect(page.get_by_role("tab")).to_have_count(0)
        checks.append("Enter/Space activation and day-switch reset")
        page.get_by_role("link", name="RAN#113 Ended").click()
        assert "/ran-plenary/" in page.url
        expect(page.locator("#agreement-panel")).to_have_count(0)
        page.locator(".day-panel.active .session-block").first.click()
        expect(page.locator("#popup-floating")).to_have_class("popup-floating show")
        page.go_back()
        expect(page.locator("#agreement-panel")).to_be_visible()
        checks.append("WG navigation/history and existing RAN Plenary popup")
        page.reload(wait_until="networkidle")
        page.evaluate("""() => {
            const original = window.fetch; let first = true;
            window.fetch = async (...args) => {
                const response = await original(...args);
                if (String(args[0]).includes('/agreements/') && first) {
                    first = false; await new Promise(resolve => setTimeout(resolve, 400));
                }
                return response;
            };
        }""")
        select("Evaluation assumptions, AI 10.1")
        expect(page.get_by_text("Loading agreements…")).to_be_visible()
        select("Energy efficiency, AI 10.4")
        expect(doc.locator("article")).to_have_count(1)
        page.wait_for_timeout(500)
        expect(doc.locator("article")).to_have_count(1)
        checks.append("Rapid selection ignores stale responses")
        page.reload(wait_until="networkidle")
        page.route(
            "**/agreements/*.html",
            lambda route: route.fulfill(status=503, body="Unavailable"),
        )
        select("Evaluation assumptions, AI 10.1")
        expect(page.get_by_role("button", name="Retry", exact=True)).to_be_visible()
        page.unroute("**/agreements/*.html")
        close_popup()
        page.get_by_role("button", name="Retry", exact=True).click()
        expect(doc.locator("article")).to_have_count(1)
        checks.append("HTML fetch failure and retry")
        # Fetched HTML must not execute scripts, load remote images or affect page CSS.
        page.reload(wait_until="networkidle")
        page.route(
            "**/agreements/*.html",
            lambda route: route.fulfill(
                body="""
            <style>body{background:red}</style><script>window.agreementXSS=1</script>
            <article><p style="color:red;background-image:url(https://invalid.example/track)">Sanitized content</p>
            <img src="https://invalid.example/track" onerror="window.agreementXSS=1">
            <iframe srcdoc="evil"></iframe><a href="javascript:alert(1)">unsafe link</a></article>"""
            ),
        )
        select("Evaluation assumptions, AI 10.1")
        expect(doc.get_by_text("Sanitized content")).to_be_visible()
        assert page.evaluate("window.agreementXSS") is None
        assert doc.locator("iframe, script, img").count() == 0
        assert doc.locator("a").get_attribute("href") is None
        assert doc.locator("p").get_attribute("style") == "color: red;"
        assert not any("invalid.example" in u for u in requests)
        page.unroute("**/agreements/*.html")
        checks.append(
            "Fetched HTML sanitization blocks active content, remote assets and CSS URLs"
        )
        page.goto(base + "/current-ran1/", wait_until="networkidle")
        page.locator(".day-panel.active .session-block").first.click()
        expect(
            page.get_by_text("No chairman note is available for this meeting.")
        ).to_be_visible()
        expect(page.locator(".agreement-document")).to_have_count(0)
        checks.append("RAN1#126 does not borrow #124 agreements")
        mobile = browser.new_page(
            viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True
        )
        mobile.goto(base + "/ran1/", wait_until="networkidle")
        mobile.get_by_role(
            "button", name="Evaluation assumptions, AI 10.1", exact=True
        ).tap()
        expect(mobile.locator(".agreement-document article")).to_have_count(1)
        expect(mobile.locator("#popup-floating")).to_have_class("popup-floating show")
        close_popup(mobile)
        mobile.locator(".agreement-header").evaluate(
            '(e)=>e.scrollIntoView({block:"start"})'
        )
        assert mobile.locator("#agreement-panel").evaluate(
            "(e)=>e.getBoundingClientRect().right <= innerWidth"
        )
        assert mobile.locator(".agreement-document").evaluate(
            "(e)=>e.scrollHeight === e.clientHeight"
        )
        mobile.screenshot(path=str(args.directory / "mobile.png"))
        checks.append("Mobile touch, preserved popup and naturally expanding document")
        offline = browser.new_page()
        offline.route("https://cdn.jsdelivr.net/**", lambda route: route.abort())
        offline.goto(base + "/ran1/", wait_until="networkidle")
        offline.get_by_role(
            "button", name="Evaluation assumptions, AI 10.1", exact=True
        ).click()
        expect(offline.get_by_role("button", name="Reload viewer")).to_be_visible()
        expect(offline.locator("#popup-floating")).to_have_class("popup-floating show")
        checks.append("CDN failure leaves original popup usable")
        assert not errors, errors
        browser.close()
    server.shutdown()
    for check in checks:
        print("PASS", check)
    print(f"{len(checks)} browser checks passed")


if __name__ == "__main__":
    main()
