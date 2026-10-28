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
    style.textContent = '#' + CSS.escape(chosen.dataset.day) + '.day-panel { display: block; }';
    document.head.appendChild(style);
})();
