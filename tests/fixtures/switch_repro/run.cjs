// Opens switch-repro.html in the bundled Chromium, runs the switch held at a
// fraction of its time, and prints the page's own read-out (window.__sample)
// as JSON, as tests/test_switch_repro.py does.
//   node run.cjs <url of switch-repro.html> same|cross|before|after move 0.5 [screenshot.png]
// (before and after tap those buttons, which pick their own variant)
const { chromium, devices } = require('playwright');
const [url, mode = 'same', variant = 'move', hold = '0.5', shot = ''] = process.argv.slice(2);
(async () => {
    const browser = await chromium.launch();
    const ctx = await browser.newContext({ ...devices['Pixel 7'], deviceScaleFactor: 1 });
    const page = await ctx.newPage();
    const query = `?page=a&v=${variant}&hold=${hold}`;
    await page.goto(url + query, { waitUntil: 'load' });
    await page.waitForTimeout(200);
    if (mode === 'same') await page.click('#switch-same');
    else if (mode === 'before' || mode === 'after') await page.click('#switch-' + mode);
    else await page.evaluate(() => document.getElementById('switch-page').click());
    await page.waitForFunction(() => window.__vt && window.__vt !== 'pending', null, { timeout: 5000 }).catch(() => {});
    const out = await page.evaluate(() => ({ state: window.__vt, ...(window.__sample || {}) }));
    if (shot) { await page.screenshot({ path: shot }); out.screenshot = shot; }
    await ctx.close();
    await browser.close();
    console.log(JSON.stringify(out));
})();
