// Runs right after the day tabs, before the day panels are parsed: the chosen
// day's grid is drawn in place as it arrives, so the sections below never
// appear in its spot first and get pushed down. schedule.js takes over at
// DOMContentLoaded by clicking the tab marked here.
(function() {
    const STATE_KEY = '3gpp_schedule_state:' + {{STATE_ID_JSON}};
    const MEETING_TZ = {{MEETING_TIMEZONE_JSON}};
    const tab = day => day ? document.querySelector('.tab[data-day="' + day + '"]') : null;
    let saved = null;
    try {
        saved = JSON.parse(sessionStorage.getItem(STATE_KEY) || 'null');
    } catch (e) {
        // sessionStorage may be unavailable
    }
    // The tab the visitor left (this tab session), else today at the venue, else the first.
    let chosen;
    if (saved && saved.activeDay) {
        chosen = tab(saved.activeDay);
    } else {
        const today = new Intl.DateTimeFormat('en-US', { timeZone: MEETING_TZ, weekday: 'long' }).format(new Date());
        chosen = tab(today.toLowerCase());
    }
    chosen = chosen || document.querySelector('.tab');
    if (!chosen) return;
    chosen.classList.add('active');
    const style = document.createElement('style');
    style.id = 'initial-day';
    // The names give the switch's animation its grid (schedule.css, @view-transition).
    const panel = '#' + CSS.escape(chosen.dataset.day) + '.day-panel';
    style.textContent = panel + ' { display: block; }'
        + ' ' + panel + ' .grid-wrapper { view-transition-name: grid-frame; view-transition-class: grid-move; }'
        + ' ' + panel + ' .schedule-grid { view-transition-name: grid-cells; }';
    document.head.appendChild(style);

    // The grid zoom it was left at comes back with the day, as in schedule.js's
    // setGridZoom. Left to DOMContentLoaded, a phone draws the grid at 100% while
    // the page arrives (and in the switch's cross-fade), then shrinks it.
    // The tab bar above is as wide as the grid's box, which is not parsed yet.
    const zoom = saved && saved.activeDay === chosen.dataset.day ? Number(saved.gridZoom) : 1;
    if (!(zoom > 0) || zoom === 1 || !(CSS.supports && CSS.supports('zoom', '1'))) return;
    const root = document.documentElement;
    const toolbar = document.querySelector('.schedule-toolbar');
    const width = toolbar ? toolbar.clientWidth : 0;
    const minWidth = parseFloat(getComputedStyle(root).getPropertyValue('--grid-min-width')) || 600;
    // Never below the zoom that fits the day (the screen may have turned since).
    const z = width ? Math.max(zoom, Math.min(1, width / minWidth)) : zoom;
    if (z === 1) return;
    root.style.setProperty('--grid-zoom', String(z));
    if (z > 1) root.style.setProperty('--axis-unzoom', String(1 / z));
    root.style.setProperty('--grid-width', Math.max(width, minWidth) + 'px');
})();
