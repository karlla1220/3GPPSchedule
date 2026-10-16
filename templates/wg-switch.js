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

    if (unseen()) hint.hidden = false;
    hint.querySelector('button').addEventListener('click', dismiss);
    menu.addEventListener('toggle', function() {
        if (menu.open) dismiss();
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
