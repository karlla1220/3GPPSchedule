// Runs right after the status line. "Experimental" explains itself on a click.
(function() {
    const toggle = document.getElementById('wg-experimental');
    const note = document.getElementById('wg-experimental-note');

    function show(open) {
        note.hidden = !open;
        toggle.setAttribute('aria-expanded', String(open));
    }

    toggle.addEventListener('click', function() { show(note.hidden); });
    // Capture: session cells stop their clicks from bubbling.
    document.addEventListener('click', function(event) {
        if (!note.hidden && event.target !== toggle && !note.contains(event.target)) show(false);
    }, true);
    document.addEventListener('keydown', function(event) {
        if (event.key === 'Escape' && !note.hidden) {
            show(false);
            toggle.focus();
        }
    });
})();
