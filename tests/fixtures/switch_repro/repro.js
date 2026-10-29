// Runs in <head> before anything is drawn (the class must be on <html> when
// the new page's first frame is captured). ?v=plain|move|fix picks the variant
// (default move), ?slow=1 slows every animation ten times. Links keep the query.
(function () {
    var q = new URLSearchParams(location.search);
    var v = q.get('v');
    document.documentElement.className = (v === 'plain' || v === 'fix' ? v : 'move') + (q.get('slow') ? ' slow' : '');
    document.addEventListener('DOMContentLoaded', function () {
        document.querySelectorAll('a[data-switch]').forEach(function (a) { a.href = a.dataset.switch + location.search; });
        var slow = document.getElementById('slow');
        if (slow) { slow.checked = !!q.get('slow'); slow.onchange = function () {
            q.set('slow', slow.checked ? '1' : ''); if (!slow.checked) q.delete('slow'); location.search = q.toString(); }; }
    });
})();
