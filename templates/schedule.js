document.addEventListener('DOMContentLoaded', function() {
    const MEETING_TZ = {{MEETING_TIMEZONE_JSON}};
    const AUTO_REFRESH_MS = {{AUTO_REFRESH_MS}}; // {{AUTO_REFRESH_MINUTES}} minutes
    const STATE_KEY = '3gpp_schedule_state';
    const NOW_TOGGLE_KEY = '3gpp_schedule_show_now';
    let showNowLine = true;

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
            weekday: 'long',
            hour12: false
        });
        const parts = fmt.formatToParts(now);
        let hour = 0, minute = 0, weekday = '';
        for (const p of parts) {
            if (p.type === 'hour') hour = parseInt(p.value, 10);
            if (p.type === 'minute') minute = parseInt(p.value, 10);
            if (p.type === 'weekday') weekday = p.value.toLowerCase();
        }
        return { hour, minute, weekday, minutes: hour * 60 + minute };
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
        btn.title = showNowLine ? 'Hide NOW line' : 'Show NOW line';
    }

    showNowLine = loadNowToggleState();
    syncNowToggleButton();

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
        });
    });

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
        const { minutes, weekday } = nowInMeetingTZ();
        const base = 8 * 60 + 30; // 08:30
        const end = 19 * 60 + 45; // 19:45

        document.querySelectorAll('.now-line').forEach(el => el.remove());

        if (!showNowLine) return;

        if (minutes >= base && minutes <= end) {
            const slot = Math.floor((minutes - base) / 5);
            const row = slot + 2;
            const todayPanel = document.getElementById(weekday);
            if (todayPanel) {
                const grid = todayPanel.querySelector('.schedule-grid');
                if (grid) {
                    const nowLine = document.createElement('div');
                    nowLine.className = 'now-line';
                    nowLine.style.gridRow = row + ' / ' + (row + 1);
                    grid.appendChild(nowLine);
                }
            }
        }
    }

    updateNowLine();
    setInterval(updateNowLine, 60000);

    const nowToggle = document.getElementById('now-toggle');
    if (nowToggle) {
        nowToggle.addEventListener('click', function() {
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

    document.querySelectorAll('.session-block').forEach(block => {
        block.addEventListener('click', function(e) {
            e.stopPropagation();
            const html = this.getAttribute('data-popup');
            if (!html) return;
            const wasOpen = popupEl.classList.contains('show');
            closePopup();
            if (!wasOpen || popupContent.innerHTML !== html) {
                popupContent.innerHTML = html;
                const blockRect = this.getBoundingClientRect();
                popupEl.classList.add('show');
                backdrop.classList.add('active');
                // Initially place to the right of the block
                let left = blockRect.right + 4;
                let top = blockRect.top;
                // Measure popup after rendering
                const pRect = popupEl.getBoundingClientRect();
                // Flip left if off-screen right
                if (left + pRect.width > window.innerWidth - 8) {
                    left = blockRect.left - pRect.width - 4;
                }
                if (left < 8) left = 8;
                // Flip up if off-screen bottom
                if (top + pRect.height > window.innerHeight - 8) {
                    top = window.innerHeight - pRect.height - 8;
                }
                if (top < 8) top = 8;
                popupEl.style.left = left + 'px';
                popupEl.style.top = top + 'px';
            }
        });
    });

    backdrop.addEventListener('click', closePopup);
    popupCloseBtn.addEventListener('click', function(e) {
        e.stopPropagation();
        closePopup();
    });

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
            filterToggle.textContent = collapsed ? '\u25C0 Filter' : '\u25B6';
            try { sessionStorage.setItem('3gpp_filter_panel', collapsed ? 'c' : 'o'); } catch(e) {}
        });

        // Restore panel state
        try {
            if (sessionStorage.getItem('3gpp_filter_panel') === 'o') {
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
