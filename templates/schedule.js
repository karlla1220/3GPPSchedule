document.addEventListener('DOMContentLoaded', function() {
    const MEETING_TZ = {{MEETING_TIMEZONE_JSON}};
    const AUTO_REFRESH_MS = {{AUTO_REFRESH_MS}}; // {{AUTO_REFRESH_MINUTES}} minutes
    const STATE_KEY = '3gpp_schedule_state:' + {{STATE_ID_JSON}};
    const NOW_TOGGLE_KEY = '3gpp_schedule_show_now:' + {{STATE_ID_JSON}};
    const MEETING_START_MS = {{MEETING_START_MS}};
    const MEETING_END_MS = {{MEETING_END_MS}};
    let meetingActive = false;
    let showNowLine = false;
    let meetingBoundaryTimer;

    // --- User state persistence (sessionStorage) ---
    function saveUserState() {
        const activeTab = document.querySelector('.tab.active');
        const state = {
            activeDay: activeTab ? activeTab.dataset.day : null,
            scrollX: window.scrollX,
            scrollY: window.scrollY
        };
        try {
            sessionStorage.setItem(STATE_KEY, JSON.stringify(state));
        } catch (e) {
            // sessionStorage may be unavailable; silently ignore
        }
    }

    function loadUserState() {
        try {
            const raw = sessionStorage.getItem(STATE_KEY);
            return raw ? JSON.parse(raw) : null;
        } catch (e) {
            return null;
        }
    }

    // Helper: get current Date components in the meeting timezone
    function nowInMeetingTZ() {
        const now = new Date();
        const fmt = new Intl.DateTimeFormat('en-US', {
            timeZone: MEETING_TZ,
            hour: 'numeric', minute: 'numeric',
            weekday: 'long', year: 'numeric', month: '2-digit', day: '2-digit',
            hour12: false
        });
        const parts = fmt.formatToParts(now);
        let hour = 0, minute = 0, weekday = '';
        const dateParts = {};
        for (const p of parts) {
            dateParts[p.type] = p.value;
            if (p.type === 'hour') hour = parseInt(p.value, 10);
            if (p.type === 'minute') minute = parseInt(p.value, 10);
            if (p.type === 'weekday') weekday = p.value.toLowerCase();
        }
        return { hour, minute, weekday, date: dateParts.year + "-" + dateParts.month + "-" + dateParts.day, minutes: (hour % 24) * 60 + minute };
    }

    // Update the "Updated" display in meeting timezone
    function updateTimeDisplay() {
        const el = document.getElementById('tz-now');
        if (!el) return;
        const now = new Date();
        const formatted = now.toLocaleString('en-US', {
            timeZone: MEETING_TZ,
            year: 'numeric', month: '2-digit', day: '2-digit',
            hour: '2-digit', minute: '2-digit', hour12: false
        });
        el.textContent = formatted;
    }
    updateTimeDisplay();
    setInterval(updateTimeDisplay, 60000);

    function loadNowToggleState() {
        try {
            const raw = localStorage.getItem(NOW_TOGGLE_KEY);
            return raw === null ? true : raw !== 'false';
        } catch (e) {
            return true;
        }
    }

    function saveNowToggleState(value) {
        try {
            localStorage.setItem(NOW_TOGGLE_KEY, value ? 'true' : 'false');
        } catch (e) {
            // localStorage may be unavailable; silently ignore
        }
    }

    function syncNowToggleButton() {
        const btn = document.getElementById('now-toggle');
        if (!btn) return;
        btn.setAttribute('aria-pressed', showNowLine ? 'true' : 'false');
        btn.disabled = !meetingActive;
        btn.title = !meetingActive ? 'NOW is available during the meeting' :
            (showNowLine ? 'Hide NOW line' : 'Show NOW line');
    }

    function refreshNowState() {
        const now = Date.now();
        const active = Number.isFinite(MEETING_START_MS) && Number.isFinite(MEETING_END_MS) &&
            MEETING_START_MS <= now && now <= MEETING_END_MS;
        if (active !== meetingActive) {
            meetingActive = active;
            showNowLine = active && loadNowToggleState();
        }
        if (!active) showNowLine = false;
        syncNowToggleButton();

        // Update at the exact boundary even between the minute ticks. Long
        // delays are capped at the browser timeout limit and re-evaluated.
        clearTimeout(meetingBoundaryTimer);
        const nextBoundary = [MEETING_START_MS, MEETING_END_MS === null ? null : MEETING_END_MS + 1]
            .find(value => Number.isFinite(value) && value > now);
        if (nextBoundary !== undefined) {
            meetingBoundaryTimer = setTimeout(updateNowLine, Math.min(nextBoundary - now, 2147483647));
        }
    }

    // Tab switching
    const tabs = document.querySelectorAll('.tab');
    const panels = document.querySelectorAll('.day-panel');

    tabs.forEach(tab => {
        tab.addEventListener('click', function() {
            tabs.forEach(t => t.classList.remove('active'));
            panels.forEach(p => p.classList.remove('active'));
            this.classList.add('active');
            const day = this.dataset.day;
            const panel = document.getElementById(day);
            if (panel) panel.classList.add('active');
            saveUserState();
        });
    });

    window.addEventListener('pagehide', saveUserState);

    // Restore saved state or auto-select today's tab
    const saved = loadUserState();
    if (saved && saved.activeDay) {
        const savedTab = document.querySelector('[data-day="' + saved.activeDay + '"]');
        if (savedTab) {
            savedTab.click();
            window.scrollTo(saved.scrollX || 0, saved.scrollY || 0);
        } else {
            const firstTab = document.querySelector('.tab');
            if (firstTab) firstTab.click();
        }
    } else {
        const { weekday: today } = nowInMeetingTZ();
        const todayTab = document.querySelector(`[data-day="${today}"]`);
        if (todayTab) {
            todayTab.click();
        } else {
            const firstTab = document.querySelector('.tab');
            if (firstTab) firstTab.click();
        }
    }

    // Now-line: update position every minute (in meeting timezone)
    // Only show on the panel matching today's weekday
    function updateNowLine() {
        refreshNowState();
        const { minutes, weekday, date } = nowInMeetingTZ();
        document.querySelectorAll('.now-line').forEach(el => el.remove());
        if (!showNowLine) return;
        const todayPanel = document.getElementById(weekday);
        if (!todayPanel || (todayPanel.dataset.date && todayPanel.dataset.date !== date)) return;
        const grid = todayPanel.querySelector('.schedule-grid');
        if (!grid) return;
        const base = Number(grid.dataset.start);
        const end = Number(grid.dataset.end);
        const interval = Number(grid.dataset.slot);
        if (minutes >= base && minutes < end) {
            const row = Math.floor((minutes - base) / interval) + 2;
            const nowLine = document.createElement('div');
            nowLine.className = 'now-line';
            nowLine.style.gridRow = row + ' / ' + (row + 1);
            grid.appendChild(nowLine);
        }
    }

    updateNowLine();
    setInterval(updateNowLine, 60000);
    document.addEventListener('visibilitychange', updateNowLine);
    window.addEventListener('focus', updateNowLine);

    const nowToggle = document.getElementById('now-toggle');
    if (nowToggle) {
        nowToggle.addEventListener('click', function() {
            refreshNowState();
            if (!meetingActive) return;
            showNowLine = !showNowLine;
            saveNowToggleState(showNowLine);
            syncNowToggleButton();
            updateNowLine();
        });
    }

    // Click-to-show popup on session blocks (shared floating popup)
    const backdrop = document.getElementById('popup-backdrop');
    const popupEl = document.getElementById('popup-floating');
    const popupContent = document.getElementById('popup-content');
    const popupCloseBtn = document.getElementById('popup-close-btn');

    function closePopup() {
        popupEl.classList.remove('show');
        backdrop.classList.remove('active');
    }

    function isPopupOpen() {
        return popupEl.classList.contains('show');
    }

    // The part of the page actually on screen, in the coordinates that
    // position: fixed and getBoundingClientRect use. On phones this is smaller
    // than the layout viewport while the browser toolbar shows or the page is
    // pinch-zoomed, so measuring documentElement or 100vh puts the popup's
    // bottom under the toolbar.
    function visibleArea() {
        const vv = window.visualViewport;
        const de = document.documentElement;
        if (!vv) return { left: 0, top: 0, width: de.clientWidth, height: de.clientHeight };
        return { left: vv.offsetLeft, top: vv.offsetTop, width: vv.width, height: vv.height };
    }

    const POPUP_MARGIN = 8;

    // Size the popup to the visible area; it scrolls inside when taller.
    function fitPopupSize(area) {
        popupEl.style.maxHeight = (area.height - 2 * POPUP_MARGIN) + 'px';
        popupEl.style.maxWidth = Math.min(520, area.width - 2 * POPUP_MARGIN) + 'px';
    }

    // Keep the popup inside the visible area: try right of the block, then
    // left, then below, then above; clamp whatever wins to the visible area.
    function placePopup(blockRect) {
        const M = POPUP_MARGIN;
        const GAP = 4;  // gap between block and popup
        const area = visibleArea();
        const minX = area.left + M;
        const maxX = area.left + area.width - M;
        const minY = area.top + M;
        const maxY = area.top + area.height - M;
        fitPopupSize(area);
        popupEl.style.width = '';
        popupEl.style.left = minX + 'px';
        popupEl.style.top = minY + 'px';
        const pRect = popupEl.getBoundingClientRect();
        const w = pRect.width;
        const h = pRect.height;
        // Hold the measured width. Otherwise a spot near the right edge
        // squeezes the popup narrower, its text wraps, and it grows taller
        // than the height it was placed by, past the bottom of the screen.
        popupEl.style.width = w + 'px';
        const clamp = (v, lo, hi) => Math.max(lo, Math.min(v, hi));
        const clampX = v => clamp(v, minX, Math.max(minX, maxX - w));
        const clampY = v => clamp(v, minY, Math.max(minY, maxY - h));
        let left;
        let top;
        if (blockRect.right + GAP + w <= maxX) {
            left = blockRect.right + GAP;
            top = clampY(blockRect.top);
        } else if (blockRect.left - GAP - w >= minX) {
            left = blockRect.left - GAP - w;
            top = clampY(blockRect.top);
        } else if (blockRect.bottom + GAP + h <= maxY) {
            left = clampX(blockRect.left);
            top = blockRect.bottom + GAP;
        } else if (blockRect.top - GAP - h >= minY) {
            left = clampX(blockRect.left);
            top = blockRect.top - GAP - h;
        } else {
            left = clampX(blockRect.left);
            top = clampY(blockRect.top);
        }
        // A block can sit partly or wholly off screen (the grid scrolls
        // sideways), so every side is clamped, then checked once more.
        popupEl.style.left = clampX(left) + 'px';
        popupEl.style.top = clampY(top) + 'px';
        keepPopupInViewport();
    }

    // After the content or the visible area changes, keep the popup where it
    // is unless part of it would leave the screen; then move it just enough.
    function keepPopupInViewport() {
        if (!isPopupOpen()) return;
        const M = POPUP_MARGIN;
        const area = visibleArea();
        fitPopupSize(area);
        const rect = popupEl.getBoundingClientRect();
        const clamp = (v, lo, hi) => Math.max(lo, Math.min(v, hi));
        const left = clamp(rect.left, area.left + M, Math.max(area.left + M, area.left + area.width - M - rect.width));
        const top = clamp(rect.top, area.top + M, Math.max(area.top + M, area.top + area.height - M - rect.height));
        popupEl.style.left = left + 'px';
        popupEl.style.top = top + 'px';
    }

    function syncAgendaToggle() {
        const open = popupEl.classList.contains('show-agenda-parents');
        popupContent.querySelectorAll('.popup-agenda-toggle').forEach(btn => {
            btn.setAttribute('aria-expanded', String(open));
        });
    }

    // Parent agenda items: one toggle, remembered for the next popup.
    popupContent.addEventListener('click', function(e) {
        const btn = e.target instanceof Element && e.target.closest('.popup-agenda-toggle');
        if (!btn) return;
        e.stopPropagation();
        popupEl.classList.toggle('show-agenda-parents');
        syncAgendaToggle();
        keepPopupInViewport();
    });

    // A wheel over the popup never scrolls the page behind it (which would
    // close the popup); it only scrolls the popup itself when that can move.
    popupEl.addEventListener('wheel', function(e) {
        const max = popupEl.scrollHeight - popupEl.clientHeight;
        const atTop = popupEl.scrollTop <= 0 && e.deltaY < 0;
        const atBottom = popupEl.scrollTop >= max - 1 && e.deltaY > 0;
        if (max <= 0 || atTop || atBottom) e.preventDefault();
    }, { passive: false });
    // Same for touch: a drag on a popup that cannot scroll stays put.
    popupEl.addEventListener('touchmove', function(e) {
        if (popupEl.scrollHeight <= popupEl.clientHeight) e.preventDefault();
    }, { passive: false });

    const agreementPanel = document.getElementById('agreement-panel');
    if (agreementPanel) {
        document.querySelectorAll('.tab').forEach(tab => tab.addEventListener('click', () => {
            document.querySelectorAll('.session-block[aria-pressed="true"]').forEach(block => block.setAttribute('aria-pressed', 'false'));
            window.dispatchEvent(new CustomEvent('agreement-select', { detail: null }));
        }));
    }
    document.querySelectorAll('.session-block').forEach(block => {
        if (agreementPanel) block.addEventListener('keydown', e => {
            if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); block.click(); }
        });
        block.addEventListener('click', function(e) {
            e.stopPropagation();
            if (agreementPanel) {
                document.querySelectorAll('.session-block[aria-pressed="true"]').forEach(item => item.setAttribute('aria-pressed', 'false'));
                this.setAttribute('aria-pressed', 'true');
                window.dispatchEvent(new CustomEvent('agreement-select', { detail: {
                    block: this, name: this.dataset.name,
                    ais: [...new Set((this.dataset.ai || '').split('|').filter(Boolean))]
                }}));
            }
            const html = this.getAttribute('data-popup');
            if (!html) return;
            const wasOpen = isPopupOpen();
            closePopup();
            if (!wasOpen || popupContent.innerHTML !== html) {
                popupContent.innerHTML = html;
                syncAgendaToggle();
                popupEl.scrollTop = 0;
                popupEl.classList.add('show');
                backdrop.classList.add('active');
                placePopup(this.getBoundingClientRect());
            }
        });
    });

    backdrop.addEventListener('click', closePopup);
    popupCloseBtn.addEventListener('click', function(e) {
        e.stopPropagation();
        closePopup();
    });

    // Scrolling anywhere (page or grid) closes the popup, since it no longer
    // sits next to its block. Scrolling inside a tall popup does not.
    function closeOnScroll(e) {
        if (!isPopupOpen()) return;
        if (e.target instanceof Node && popupEl.contains(e.target)) return;
        closePopup();
    }
    document.addEventListener('scroll', closeOnScroll, { capture: true, passive: true });
    // A scroll gesture on the backdrop closes it even when the page cannot
    // move (already at the edge, or only the grid scrolls horizontally).
    backdrop.addEventListener('wheel', closePopup, { passive: true });
    backdrop.addEventListener('touchmove', closePopup, { passive: true });
    // A phone's toolbar showing or hiding changes only the height: keep the
    // popup and fit it to the new visible area. A width change (rotation, a
    // resized window) moves the grid under it, so close.
    let lastViewportWidth = document.documentElement.clientWidth;
    window.addEventListener('resize', function() {
        const width = document.documentElement.clientWidth;
        if (width !== lastViewportWidth) {
            lastViewportWidth = width;
            closePopup();
        } else {
            keepPopupInViewport();
        }
    });
    // Pinch-zoom and panning move the visible area without scrolling the page.
    if (window.visualViewport) {
        window.visualViewport.addEventListener('resize', keepPopupInViewport);
        window.visualViewport.addEventListener('scroll', keepPopupInViewport);
    }

    // ── Session Filter ──
    const filterDataEl = document.getElementById('filter-data');
    if (filterDataEl) {
        const FD = JSON.parse(filterDataEl.textContent);
        const filterPanel = document.querySelector('.filter-panel');
        const filterToggle = document.querySelector('.filter-toggle');
        const filterClear = document.querySelector('.filter-clear');
        const filterList = document.querySelector('.filter-list');
        const filterCount = document.querySelector('.filter-active-count');
        const dimOpacityRange = document.querySelector('.filter-dim-opacity-range');
        const dimOpacityValue = document.querySelector('.filter-dim-opacity-value');
        const DIM_OPACITY_DEFAULT = 0.30;
        // Three sets are the source of truth; group/session visual state is DERIVED.
        const activeSessions = new Set();  // keys of sessions WITHOUT AIs
        const activeAIs = new Set();
        const activeNoAISessions = new Set(); // keys of sessions with AIs that also have no-AI blocks
        let dimOpacity = DIM_OPACITY_DEFAULT;

        function clampDimOpacity(v) {
            var n = Number(v);
            if (!isFinite(n)) return DIM_OPACITY_DEFAULT;
            if (n < 0.02) return 0.02;
            if (n > 0.95) return 0.95;
            return Math.round(n * 100) / 100;
        }

        function setDimOpacity(v) {
            dimOpacity = clampDimOpacity(v);
            document.documentElement.style.setProperty('--dim-opacity', String(dimOpacity));
            if (dimOpacityRange) dimOpacityRange.value = String(dimOpacity);
            if (dimOpacityValue) dimOpacityValue.textContent = Math.round(dimOpacity * 100) + '%';
        }

        // --- helpers to look up FD ---
        function findGroup(key) { return FD.groups.find(function(g){ return g.key===key; }); }
        function findSess(key) {
            var out = null;
            FD.groups.forEach(function(g){ g.sessions.forEach(function(s){ if(s.key===key) out=s; }); });
            return out;
        }

        function mkEl(tag, cls) { const e = document.createElement(tag); if (cls) e.className = cls; return e; }
        function mkSpacer() { const s = document.createElement('span'); s.style.width='16px'; s.style.flexShrink='0'; return s; }
        function mkToggle(container) {
            const btn = mkEl('button','tree-toggle');
            btn.textContent = '\u25B6';
            btn.addEventListener('click', function(e) {
                e.stopPropagation();
                const ch = container.querySelector(':scope > .filter-children');
                if (!ch) return;
                const exp = ch.classList.toggle('expanded');
                btn.textContent = exp ? '\u25BC' : '\u25B6';
            });
            return btn;
        }

        function buildFilterList() {
            filterList.innerHTML = '';
            // --- Group trees ---
            FD.groups.forEach(function(group, gi) {
                const grpDiv = mkEl('div','filter-group');
                // Group header row
                const grpRow = mkEl('div','filter-item');
                grpRow.appendChild(mkToggle(grpDiv));
                const gcb = document.createElement('input');
                gcb.type = 'checkbox'; gcb.id = 'fg'+gi; gcb.dataset.gk = group.key;
                gcb.addEventListener('change', function() { onGroupChange(group.key, gcb.checked); });
                grpRow.appendChild(gcb);
                const gl = document.createElement('label'); gl.htmlFor = gcb.id;
                gl.textContent = group.name; gl.title = group.name;
                grpRow.appendChild(gl);
                grpDiv.appendChild(grpRow);

                // Sessions under this group
                const sessC = mkEl('div','filter-children');
                group.sessions.forEach(function(sess, si) {
                    const sessWrap = mkEl('div','filter-group');
                    const sessRow = mkEl('div','filter-item');
                    if (sess.ais.length > 0) {
                        sessRow.appendChild(mkToggle(sessWrap));
                    } else {
                        sessRow.appendChild(mkSpacer());
                    }
                    const scb = document.createElement('input');
                    scb.type = 'checkbox'; scb.id = 'fs'+gi+'_'+si; scb.dataset.sk = sess.key;
                    scb.addEventListener('change', function() { onSessionChange(sess.key, scb.checked); });
                    sessRow.appendChild(scb);
                    const sl = document.createElement('label'); sl.htmlFor = scb.id;
                    sl.textContent = sess.name; sl.title = sess.name;
                    sessRow.appendChild(sl);
                    sessWrap.appendChild(sessRow);

                    // AIs under this session
                    if (sess.ais.length > 0) {
                        const aiC = mkEl('div','filter-children');
                        sess.ais.forEach(function(ai, ai_i) {
                            const aiRow = mkEl('div','filter-item');
                            aiRow.appendChild(mkSpacer());
                            const acb = document.createElement('input');
                            acb.type = 'checkbox'; acb.id = 'fsa'+gi+'_'+si+'_'+ai_i; acb.dataset.ai = ai;
                            acb.addEventListener('change', function() { onAIChange(ai, acb.checked); });
                            aiRow.appendChild(acb);
                            const al = document.createElement('label'); al.htmlFor = acb.id;
                            al.textContent = 'AI '+ai;
                            aiRow.appendChild(al);
                            aiC.appendChild(aiRow);
                        });
                        // "Not assigned" entry for sessions that have AI-less blocks
                        if (sess.hasNoAI) {
                            const naRow = mkEl('div','filter-item');
                            naRow.appendChild(mkSpacer());
                            const nacb = document.createElement('input');
                            nacb.type = 'checkbox'; nacb.id = 'fsna'+gi+'_'+si;
                            nacb.dataset.noai = sess.key;
                            nacb.addEventListener('change', function() { onNoAIChange(sess.key, nacb.checked); });
                            naRow.appendChild(nacb);
                            const nal = document.createElement('label'); nal.htmlFor = nacb.id;
                            nal.textContent = 'Not assigned';
                            naRow.appendChild(nal);
                            aiC.appendChild(naRow);
                        }
                        sessWrap.appendChild(aiC);
                    }
                    sessC.appendChild(sessWrap);
                });
                grpDiv.appendChild(sessC);
                filterList.appendChild(grpDiv);
            });

            // --- Separator + flat AI list ---
            if (FD.allAIs.length > 0) {
                const sep = mkEl('div','filter-separator');
                sep.textContent = '\u2500\u2500 AI \u2500\u2500';
                filterList.appendChild(sep);
                FD.allAIs.forEach(function(ai, i) {
                    const row = mkEl('div','filter-item');
                    row.appendChild(mkSpacer());
                    const cb = document.createElement('input');
                    cb.type = 'checkbox'; cb.id = 'fa'+i; cb.dataset.ai = ai;
                    cb.addEventListener('change', function() { onAIChange(ai, cb.checked); });
                    row.appendChild(cb);
                    const lb = document.createElement('label'); lb.htmlFor = cb.id;
                    lb.textContent = 'AI '+ai;
                    row.appendChild(lb);
                    filterList.appendChild(row);
                });
            }
        }

        // ── Cascade handlers ──

        // Group click → cascade to all child sessions → AIs + noAI
        function onGroupChange(key, checked) {
            var group = findGroup(key);
            if (!group) return;
            group.sessions.forEach(function(sess) {
                if (sess.ais.length > 0) {
                    sess.ais.forEach(function(ai) {
                        if (checked) activeAIs.add(ai); else activeAIs.delete(ai);
                    });
                    if (sess.hasNoAI) {
                        if (checked) activeNoAISessions.add(sess.key); else activeNoAISessions.delete(sess.key);
                    }
                } else {
                    if (checked) activeSessions.add(sess.key); else activeSessions.delete(sess.key);
                }
            });
            syncCheckboxes(); applyFilter(); updateFilterHash();
        }

        // Session click → cascade to child AIs + noAI
        function onSessionChange(key, checked) {
            var sess = findSess(key);
            if (!sess) return;
            if (sess.ais.length > 0) {
                sess.ais.forEach(function(ai) {
                    if (checked) activeAIs.add(ai); else activeAIs.delete(ai);
                });
                if (sess.hasNoAI) {
                    if (checked) activeNoAISessions.add(key); else activeNoAISessions.delete(key);
                }
            } else {
                if (checked) activeSessions.add(key); else activeSessions.delete(key);
            }
            syncCheckboxes(); applyFilter(); updateFilterHash();
        }

        // AI click → just toggle the AI; parents derive visually
        function onAIChange(ai, checked) {
            if (checked) activeAIs.add(ai); else activeAIs.delete(ai);
            syncCheckboxes(); applyFilter(); updateFilterHash();
        }

        // "Not assigned" click → toggle the session's no-AI flag
        function onNoAIChange(sessKey, checked) {
            if (checked) activeNoAISessions.add(sessKey); else activeNoAISessions.delete(sessKey);
            syncCheckboxes(); applyFilter(); updateFilterHash();
        }

        // ── Derive visual state from activeAIs + activeSessions + activeNoAISessions ──
        function syncCheckboxes() {
            // 1. Sync all AI checkboxes (tree duplicates + flat list)
            document.querySelectorAll('input[data-ai]').forEach(function(cb) {
                cb.checked = activeAIs.has(cb.dataset.ai);
            });

            // 1b. Sync "Not assigned" checkboxes
            document.querySelectorAll('input[data-noai]').forEach(function(cb) {
                cb.checked = activeNoAISessions.has(cb.dataset.noai);
            });

            // 2. Session checkboxes: derive from children
            FD.groups.forEach(function(group) {
                group.sessions.forEach(function(sess) {
                    var scb = document.querySelector('input[data-sk="' + sess.key + '"]');
                    if (!scb) return;
                    if (sess.ais.length > 0) {
                        var n = 0;
                        sess.ais.forEach(function(ai) { if (activeAIs.has(ai)) n++; });
                        var totalChildren = sess.ais.length;
                        var checkedChildren = n;
                        if (sess.hasNoAI) {
                            totalChildren++;
                            if (activeNoAISessions.has(sess.key)) checkedChildren++;
                        }
                        scb.checked = (checkedChildren === totalChildren);
                        scb.indeterminate = (checkedChildren > 0 && checkedChildren < totalChildren);
                    } else {
                        scb.checked = activeSessions.has(sess.key);
                        scb.indeterminate = false;
                    }
                });
            });

            // 3. Group checkboxes: derive from child sessions
            FD.groups.forEach(function(group) {
                var gcb = document.querySelector('input[data-gk="' + group.key + '"]');
                if (!gcb) return;
                var total = group.sessions.length;
                if (total === 0) { gcb.checked = false; gcb.indeterminate = false; return; }
                var full = 0, partial = 0;
                group.sessions.forEach(function(sess) {
                    var scb = document.querySelector('input[data-sk="' + sess.key + '"]');
                    if (!scb) return;
                    if (scb.checked) full++;
                    else if (scb.indeterminate) partial++;
                });
                gcb.checked = (full === total);
                gcb.indeterminate = (!gcb.checked && (full > 0 || partial > 0));
            });

            // 4. Badge count
            var total = activeAIs.size + activeSessions.size + activeNoAISessions.size;
            if (filterCount) { filterCount.textContent = total > 0 ? total : ''; }
        }

        function applyFilter() {
            var hasFilter = activeAIs.size > 0 || activeSessions.size > 0 || activeNoAISessions.size > 0;
            // Derive session keys for sessions whose ALL AIs (+ noAI) are active.
            // This ensures blocks without data-ai still match when the
            // session (or parent group) checkbox is fully checked.
            var derivedKeys = new Set(activeSessions);
            if (activeAIs.size > 0) {
                FD.groups.forEach(function(group) {
                    group.sessions.forEach(function(sess) {
                        if (sess.ais.length > 0 && sess.ais.every(function(ai) { return activeAIs.has(ai); })) {
                            if (!sess.hasNoAI || activeNoAISessions.has(sess.key)) {
                                derivedKeys.add(sess.key);
                            }
                        }
                    });
                });
            }
            document.querySelectorAll('.session-block').forEach(function(block) {
                if (!hasFilter) { block.classList.remove('dimmed'); return; }
                var grp = block.getAttribute('data-group') || '';
                var nm  = block.getAttribute('data-name') || '';
                var raw = block.getAttribute('data-ai') || '';
                var aiVals = raw.split('|').filter(function(v){ return v.trim(); });
                var sessKey = nm + '|' + grp;
                var match = derivedKeys.has(sessKey) ||
                            aiVals.some(function(v){ return activeAIs.has(v); });
                // Also match blocks with no AI if "Not assigned" is active for this session
                if (!match && aiVals.length === 0 && activeNoAISessions.has(sessKey)) {
                    match = true;
                }
                if (match) { block.classList.remove('dimmed'); } else { block.classList.add('dimmed'); }
            });
        }

        // URL hash: s:key, a:val, n:sessKey (noAI), o:dimOpacity
        function updateFilterHash() {
            var parts = [];
            activeSessions.forEach(function(v){ parts.push('s:'+encodeURIComponent(v)); });
            activeAIs.forEach(function(v){ parts.push('a:'+encodeURIComponent(v)); });
            activeNoAISessions.forEach(function(v){ parts.push('n:'+encodeURIComponent(v)); });
            if (Math.abs(dimOpacity - DIM_OPACITY_DEFAULT) > 0.0001) {
                parts.push('o:' + encodeURIComponent(dimOpacity.toFixed(2)));
            }
            if (parts.length === 0) {
                history.replaceState(null, '', location.pathname + location.search);
            } else {
                history.replaceState(null, '', '#filter=' + parts.join(','));
            }
        }

        function loadFilterHash() {
            var h = location.hash;
            if (!h || !h.startsWith('#filter=')) return;
            h.slice(8).split(',').forEach(function(tok) {
                var c = tok.indexOf(':');
                if (c < 0) return;
                var type = tok.slice(0, c);
                var val  = decodeURIComponent(tok.slice(c+1));
                if (!val) return;
                if (type === 's') activeSessions.add(val);
                else if (type === 'a') activeAIs.add(val);
                else if (type === 'n') activeNoAISessions.add(val);
                else if (type === 'o') setDimOpacity(val);
            });
            syncCheckboxes();
            applyFilter();
            // Auto-expand trees with active items
            filterList.querySelectorAll('.filter-group').forEach(function(grpEl) {
                var ch = grpEl.querySelector(':scope > .filter-children');
                if (!ch) return;
                var hasActive = ch.querySelector('input:checked') || ch.querySelector('input:indeterminate');
                if (hasActive) {
                    ch.classList.add('expanded');
                    var tog = grpEl.querySelector(':scope > .filter-item > .tree-toggle');
                    if (tog) tog.textContent = '\u25BC';
                }
            });
        }

        // Panel toggle
        filterToggle.addEventListener('click', function() {
            var collapsed = filterPanel.classList.toggle('collapsed');
            filterToggle.textContent = collapsed ? '◀ Filter' : '▶';
            try { sessionStorage.setItem(STATE_KEY + ':filter-panel', collapsed ? 'c' : 'o'); } catch(e) {}
        });

        // Restore panel state
        try {
            if (sessionStorage.getItem(STATE_KEY + ':filter-panel') === 'o') {
                filterPanel.classList.remove('collapsed');
                filterToggle.textContent = '\u25B6';
            }
        } catch(e) {}

        // Clear all
        filterClear.addEventListener('click', function() {
            activeSessions.clear(); activeAIs.clear(); activeNoAISessions.clear();
            syncCheckboxes(); applyFilter(); updateFilterHash();
        });

        if (dimOpacityRange) {
            dimOpacityRange.addEventListener('input', function() {
                setDimOpacity(dimOpacityRange.value);
                updateFilterHash();
            });
        }

        buildFilterList();
        setDimOpacity(DIM_OPACITY_DEFAULT);
        loadFilterHash();
    }

    // --- Auto-refresh: reload page periodically, preserving user state ---
    if (AUTO_REFRESH_MS > 0) {
        setInterval(function() {
            saveUserState();
            location.reload();
        }, AUTO_REFRESH_MS);
    }
});
