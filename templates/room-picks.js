// The room checkboxes of the All tab. Runs right after them, before the grid is
// parsed, so a saved choice draws from the first frame and day-select.js fits
// the zoom to the width of the rooms left. One rule does the work: renderer.py
// marks each All view element with its rooms (wr-N) and days (wd-N), and the
// grid keeps its column numbers, with a hidden room's track at no width.
(function() {
    const KEY = '3gpp_schedule_rooms:' + {{STATE_ID_JSON}};
    const box = document.querySelector('.room-picks');
    if (!box) return;
    const boxes = [...box.querySelectorAll('input[data-room]')];
    const week = JSON.parse(box.dataset.week);
    const tab = document.querySelector('.tab[data-day="all-days"]');
    const style = document.createElement('style');
    style.id = 'week-rooms';
    document.head.appendChild(style);

    // Hidden rooms are kept, not shown ones: a room added later starts ticked.
    // A room is its key (renderer.py _room_key), not its name, which may change by the day.
    function load() {
        try {
            const hidden = JSON.parse(localStorage.getItem(KEY) || '[]');
            return Array.isArray(hidden) ? hidden : [];
        } catch (e) {
            return [];
        }
    }

    function save() {
        const hidden = boxes.filter(b => !b.checked).map(b => b.dataset.key);
        try {
            if (hidden.length) localStorage.setItem(KEY, JSON.stringify(hidden));
            else localStorage.removeItem(KEY);
        } catch (e) {
            // localStorage may be unavailable; the choice lasts until the page reloads
        }
    }

    function draw() {
        const shown = new Set(boxes.filter(b => b.checked).map(b => Number(b.dataset.room)));
        // Only a last unticked box left would empty the grid: it stays ticked.
        boxes.forEach(b => { b.disabled = b.checked && shown.size === 1; });
        const showsDay = week.days.map(rooms => rooms.some(room => shown.has(room)));
        const lastDay = showsDay.lastIndexOf(true);
        const tracks = ['var(--axis-col-width)'];
        const hide = [];
        let rooms = 0;
        week.days.forEach((dayRooms, i) => {
            dayRooms.forEach(room => {
                const on = shown.has(room);
                rooms += on ? 1 : 0;
                tracks.push(on ? 'minmax(0, 1fr)' : '0px');
            });
            if (!showsDay[i]) hide.push('.wd-' + i);
            if (i < week.days.length - 1) {
                const gutter = showsDay[i] && i < lastDay;
                tracks.push(gutter ? week.gutter + 'px' : '0px');
                if (!gutter) hide.push('.wg-' + i);
            }
        });
        const days = showsDay.filter(Boolean).length;
        const width = week.axis + rooms * week.room + (days - 1) * week.gutter;
        if (tab) tab.dataset.gridMinWidth = String(width);
        if (shown.size === boxes.length) {
            style.textContent = '';
            return;
        }
        const left = [...shown].map(room => '.wr-' + room).join(',');
        style.textContent = '#all-days .week-grid { grid-template-columns: ' + tracks.join(' ') + ' !important;'
            + ' --grid-min-width: ' + width + 'px !important; }'
            + ' #all-days :is(.week-cell, .room-header:not(.time-col)):not(' + left + ') { display: none; }'
            + (hide.length ? ' #all-days :is(' + hide.join(',') + ') { display: none; }' : '');
    }

    const hidden = new Set(load());
    boxes.forEach(b => { b.checked = !hidden.has(b.dataset.key); });
    // A saved choice of rooms this meeting no longer has shows every room.
    if (!boxes.some(b => b.checked)) boxes.forEach(b => { b.checked = true; });
    draw();
    box.addEventListener('change', function() {
        save();
        draw();
        // schedule.js fits the zoom to the new width.
        document.dispatchEvent(new CustomEvent('room-picks-change'));
    });
})();
