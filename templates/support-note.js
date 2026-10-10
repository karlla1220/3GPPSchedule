// Runs right after the note in the header, before the grid below is laid out,
// so showing the note never shifts a page the visitor is already reading.
(function() {
    const note = document.getElementById('support-note');
    const SHOW_FROM_MS = {{SHOW_FROM_MS}};
    const DISMISSED_KEY = '3gpp_schedule_support_dismissed:' + {{STATE_ID_JSON}};

    function dismissed() {
        try {
            return localStorage.getItem(DISMISSED_KEY) === 'true';
        } catch (e) {
            return false;
        }
    }

    if (Date.now() >= SHOW_FROM_MS && !dismissed()) note.hidden = false;

    note.querySelector('.support-note-close').addEventListener('click', function() {
        note.hidden = true;
        try {
            localStorage.setItem(DISMISSED_KEY, 'true');
        } catch (e) {
            // localStorage may be unavailable; the note returns on the next visit
        }
    });
})();
