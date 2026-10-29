// Opens a.html, switches to b.html and holds the switch at given times, as
// tests/test_switch_repro.py does. Prints JSON: per time, the computed boxes of
// the switch's pseudo-elements and the screenshot it wrote.
//   node run.cjs http://localhost:8000/ move 100,220 /tmp/frames
// Playwright with the bundled Chromium is required (require('playwright')).
const { chromium, devices } = require('playwright');
const [base, variant = 'move', times = '100', outDir = ''] = process.argv.slice(2);
const PSEUDOS = ['::view-transition-group(root)', '::view-transition-old(root)', '::view-transition-new(root)',
                 '::view-transition-group(day-tabs)', '::view-transition-old(day-tabs)',
                 '::view-transition-group(grid-cells)', '::view-transition-old(grid-cells)', '::view-transition-new(grid-cells)',
                 '::view-transition-group(time-540)', '::view-transition-old(time-540)'];
(async () => {
    const browser = await chromium.launch();
    const out = {};
    for (const t of times.split(',').map(Number)) {
        const ctx = await browser.newContext({ ...devices['Pixel 7'], deviceScaleFactor: 1 });
        await ctx.addInitScript((t) => {
            window.addEventListener('pagereveal', (e) => {
                if (!e.viewTransition) { window.__vt = 'none'; return; }
                window.__vt = 'pending';
                e.viewTransition.ready.then(() => {
                    document.getAnimations().forEach(a => { a.pause(); a.currentTime = t; });
                    window.__vt = 'paused';
                });
            });
        }, t);
        const page = await ctx.newPage();
        const query = '?v=' + variant;
        await page.goto(base + 'a.html' + query, { waitUntil: 'load' });
        await page.waitForTimeout(300);
        const before = await page.evaluate(() => ({ width: innerWidth, height: innerHeight, variant: document.documentElement.className }));
        await page.evaluate((q) => { location.assign('b.html' + q); }, query);
        await page.waitForFunction(() => window.__vt && window.__vt !== 'pending', null, { timeout: 5000 }).catch(() => {});
        const frame = await page.evaluate((PSEUDOS) => {
            const html = document.documentElement, boxes = {};
            for (const p of PSEUDOS) {
                const s = getComputedStyle(html, p);
                boxes[p] = { width: s.width, height: s.height, objectFit: s.objectFit, transform: s.transform, opacity: s.opacity };
            }
            return { state: window.__vt, variant: html.className, viewport: { width: innerWidth, height: innerHeight }, boxes };
        }, PSEUDOS);
        if (outDir) {
            frame.screenshot = `${outDir}/${variant}-${t}.png`;
            await page.screenshot({ path: frame.screenshot });
        }
        out[t] = { before, ...frame };
        await ctx.close();
    }
    await browser.close();
    console.log(JSON.stringify(out));
})();
