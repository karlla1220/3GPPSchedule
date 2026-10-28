// Runs right after the meeting title, before the grid below is laid out.
// The title opens a menu of every WG's meeting; a one-time hint says so.
(function() {
    const menu = document.getElementById('wg-switch');
    const hint = document.getElementById('wg-switch-hint');
    const WG_IDS = {{WG_IDS_JSON}};
    // The WGs a visitor has been told about. A WG added later brings the hint back.
    const SEEN_KEY = '3gpp_schedule_wg_switch_seen';

    function unseen() {
        try {
            const seen = JSON.parse(localStorage.getItem(SEEN_KEY) || '[]');
            return WG_IDS.some(function(id) { return seen.indexOf(id) < 0; });
        } catch (e) {
            // Without storage the hint could never be dismissed for good; leave it out.
            return false;
        }
    }

    function dismiss() {
        if (hint.hidden) return;
        hint.hidden = true;
        try {
            localStorage.setItem(SEEN_KEY, JSON.stringify(WG_IDS));
        } catch (e) {
            // localStorage may be unavailable
        }
    }

    // Fetch the other meetings while the visitor is still choosing, so the
    // switch needs no network. Browsers with speculation rules (Chrome, Edge,
    // Samsung Internet) also render the active ones in the background and swap
    // them in at once; ended meetings are only fetched, a full page each costs memory.
    let warmed = false;
    function warm() {
        if (warmed) return;
        warmed = true;
        const others = Array.from(menu.querySelectorAll('.wg-switch-item:not([aria-current])'));
        const active = others.filter(a => !a.classList.contains('wg-switch-past')).map(a => a.href);
        const past = others.filter(a => a.classList.contains('wg-switch-past')).map(a => a.href);
        if (HTMLScriptElement.supports && HTMLScriptElement.supports('speculationrules')) {
            const rules = document.createElement('script');
            rules.type = 'speculationrules';
            rules.textContent = JSON.stringify({
                prerender: active.length ? [{ urls: active, eagerness: 'immediate' }] : [],
                prefetch: past.length ? [{ urls: past, eagerness: 'immediate' }] : [],
            });
            document.head.appendChild(rules);
        } else {
            active.concat(past).forEach(function(href) {
                const link = document.createElement('link');
                link.rel = 'prefetch';
                link.href = href;
                document.head.appendChild(link);
            });
        }
    }

    if (unseen()) hint.hidden = false;
    // A page rendered ahead (see warm) read the storage before the visitor
    // opened the menu on the page they came from; read it again when shown.
    document.addEventListener('prerenderingchange', function() {
        if (!unseen()) hint.hidden = true;
    });
    hint.querySelector('button').addEventListener('click', dismiss);
    // A pointer resting on the title is the earliest sign; opening it is the sure one.
    menu.querySelector('summary').addEventListener('pointerenter', warm);
    menu.addEventListener('toggle', function() {
        if (menu.open) {
            dismiss();
            warm();
        }
    });

    // Capture: session cells stop their clicks from bubbling.
    document.addEventListener('click', function(event) {
        if (menu.open && !menu.contains(event.target)) menu.open = false;
    }, true);
    document.addEventListener('keydown', function(event) {
        if (event.key === 'Escape' && menu.open) {
            menu.open = false;
            menu.querySelector('summary').focus();
        }
    });
})();
