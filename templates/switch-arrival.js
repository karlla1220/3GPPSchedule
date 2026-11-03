// Runs in <head>. Arriving from another WG's page (the meeting switch, or back
// and forward between them), the first frame waits until the day grids are
// parsed (#grid-end), so the switch's animation finds this page's grid
// (schedule.css, @view-transition): drawn earlier, the page has none to move to
// and its cells fill in over the cross-fade. The page before stays shown
// meanwhile. Only a parser-written <link> in <head> can hold the first frame.
// Entering from index.html, a reload or another site, the page draws at once.
(function() {
    try {
        if (!document.referrer) return;
        const from = new URL(document.referrer);
        const nav = performance.getEntriesByType('navigation')[0];
        // "/repo/ran1/" (or ".../index.html") -> "/repo/": the WG pages sit side by side below it.
        const site = location.pathname.replace(/index\.html$/, '').replace(/[^/]+\/$/, '');
        const rest = from.pathname.slice(site.length);
        const sibling = from.origin === location.origin && from.pathname.startsWith(site)
            && rest && rest !== 'index.html' && from.pathname !== location.pathname;
        if (sibling && !(nav && nav.type === 'reload')) {
            document.write('<link rel="expect" href="#grid-end" blocking="render">');
        }
    } catch (e) {
        // No referrer or performance entry: draw at once.
    }
})();
