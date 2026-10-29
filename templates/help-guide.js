// The Help button opens a guide of short scenes. Each scene is played on
// copies of this page's own cells (same markup, same CSS, this meeting's
// sessions), so the guide shows the page as it is now and never goes stale.
// Nothing is written to storage and the page itself is never touched.
(function() {
    const button = document.getElementById('help-button');
    const guide = document.getElementById('help-guide');
    if (!button || !guide) return;
    if (typeof guide.showModal !== 'function') {
        button.hidden = true;
        return;
    }
    const body = guide.querySelector('.help-guide-body');
    const STOP = {};
    // A scene holds its last frame this long before it plays again.
    const HOLD_MS = 2200;
    let running = [];
    let observer = null;

    function el(tag, cls) {
        const node = document.createElement(tag);
        if (cls) node.className = cls;
        return node;
    }

    // A copy that cannot clash with the page: no ids, no focus stops.
    function copy(node) {
        const clone = node.cloneNode(true);
        [clone].concat(Array.from(clone.querySelectorAll('[id]'))).forEach(n => n.removeAttribute('id'));
        [clone].concat(Array.from(clone.querySelectorAll('[tabindex]'))).forEach(n => n.removeAttribute('tabindex'));
        return clone;
    }

    function sleep(ms, token) {
        return new Promise((resolve, reject) => {
            setTimeout(() => (token.live ? resolve() : reject(STOP)), ms);
        });
    }

    function within(stage, node, fx, fy) {
        const s = stage.getBoundingClientRect();
        const r = node.getBoundingClientRect();
        return { x: r.left - s.left + r.width * fx, y: r.top - s.top + r.height * fy };
    }

    // ── The finger: a soft dot that moves and taps ──
    function finger(stage) {
        const dot = el('div', 'help-finger');
        stage.appendChild(dot);
        return dot;
    }

    function place(dot, p) {
        dot.style.transform = 'translate(' + p.x + 'px, ' + p.y + 'px)';
    }

    async function moveTo(dot, p, token, ms) {
        dot.style.transitionDuration = (ms || 650) + 'ms, 200ms';
        dot.classList.add('shown');
        place(dot, p);
        await sleep(ms || 650, token);
    }

    async function tap(dot, token) {
        dot.classList.remove('tap');
        void dot.offsetWidth;
        dot.classList.add('tap');
        await sleep(280, token);
    }

    function hide(dot, p) {
        dot.style.transitionDuration = '0ms, 200ms';
        dot.classList.remove('shown', 'tap');
        if (p) place(dot, p);
    }

    // ── This page's grid, as it is laid out on a phone ──
    function sourceGrid() {
        const grid = document.querySelector('.day-panel.active .schedule-grid') || document.querySelector('.schedule-grid');
        return grid && grid.querySelector('.session-block') ? grid : null;
    }

    function gridView(stage) {
        const source = sourceGrid();
        if (!source) return null;
        const wrapper = el('div', 'grid-wrapper help-grid');
        const grid = copy(source);
        grid.querySelectorAll('.session-block').forEach(block => {
            block.classList.remove('dimmed');
            if (block.hasAttribute('aria-pressed')) block.setAttribute('aria-pressed', 'false');
        });
        wrapper.appendChild(grid);
        stage.appendChild(wrapper);
        return { wrapper: wrapper, grid: grid };
    }

    function columns(block) {
        const parts = (block.style.gridColumn || '').split('/').map(v => parseInt(v, 10));
        const start = parts[0] || 2;
        return { start: start, span: (parts[1] || start + 1) - start };
    }

    // The cell a scene taps: a whole session with an agenda item and room for
    // its lines, near the left so the phone-wide view shows it with its time,
    // and short enough to leave room for what it opens.
    function pickBlock(grid, accept) {
        let best = null;
        let bestScore = -Infinity;
        grid.querySelectorAll('.session-block').forEach(block => {
            if (!block.dataset.popup || block.classList.contains('short-session') || block.classList.contains('tiny-session')) return;
            if (accept && !accept(block)) return;
            const col = columns(block);
            const score = (block.dataset.ai ? 4 : 0) + (block.querySelector('.session-note') ? 2 : 0) +
                (col.start <= 3 ? 2 : 0) - (col.span > 1 ? 4 : 0) - (block.offsetHeight > 150 ? 3 : 0) -
                block.offsetTop / 2000;
            if (score > bestScore) {
                best = block;
                bestScore = score;
            }
        });
        return best;
    }

    // Scroll the copy so the cell sits at the left, under its room's name when it can.
    function focus(view, block) {
        const axis = view.grid.querySelector('.room-header.time-col');
        view.wrapper.scrollLeft = Math.max(0, block.offsetLeft - (axis ? axis.offsetWidth : 36) - 6);
        const top = block.offsetTop - (axis ? axis.offsetHeight : 36);
        view.wrapper.scrollTop = top < view.wrapper.clientHeight * 0.35 ? 0 : top - 24;
    }

    function rest(stage) {
        return { x: stage.clientWidth - 30, y: stage.clientHeight + 20 };
    }

    // ── Scenes ──
    const scenes = {
        details: {
            build(stage) {
                const view = gridView(stage);
                const block = view && pickBlock(view.grid);
                if (!block) return null;
                const popup = el('div', 'popup-floating help-popup');
                const content = el('div');
                const close = el('span', 'popup-close');
                close.textContent = '×';
                popup.appendChild(content);
                popup.appendChild(close);
                stage.appendChild(popup);
                return { stage: stage, view: view, block: block, popup: popup, content: content, dot: finger(stage) };
            },
            reset(c) {
                focus(c.view, c.block);
                c.popup.classList.remove('show', 'shown');
                hide(c.dot, rest(c.stage));
            },
            open(c) {
                c.content.innerHTML = c.block.dataset.popup;
                c.popup.classList.add('show');
                placePopup(c.stage, c.popup, c.block);
                c.popup.classList.add('shown');
            },
            async play(c, token) {
                await moveTo(c.dot, within(c.stage, c.block, 0.45, 0.55), token, 800);
                await tap(c.dot, token);
                this.open(c);
                await sleep(400, token);
                await moveTo(c.dot, rest(c.stage), token, 500);
                await sleep(2400, token);
                const outside = { x: c.stage.clientWidth * 0.5, y: c.stage.clientHeight - 24 };
                await moveTo(c.dot, outside, token, 600);
                await tap(c.dot, token);
                c.popup.classList.remove('shown');
                await sleep(200, token);
                c.popup.classList.remove('show');
            },
            end(c) {
                this.open(c);
            },
        },

        agreements: {
            needs: () => !!document.getElementById('agreement-panel'),
            build(stage) {
                if (!sourceGrid()) return null;
                const page = el('div', 'help-page');
                stage.appendChild(page);
                const view = gridView(page);
                const block = view && pickBlock(view.grid, b => !!b.dataset.agreementAi);
                if (!block) return null;
                const panel = el('section', 'agreement-panel help-agreement');
                const header = el('div', 'agreement-header');
                const title = el('h2');
                title.textContent = 'Agreements for';
                header.appendChild(title);
                const status = el('p', 'agreement-status help-agreement-status');
                status.textContent = 'Select a schedule cell to read its agreements here.';
                const text = el('div', 'help-agreement-body');
                const heading = el('p', 'help-agreement-ai');
                heading.textContent = 'AI ' + block.dataset.agreementAi.split('|')[0];
                text.appendChild(heading);
                [0.92, 0.78, 0.86, 0.64, 0.9, 0.7].forEach((width, i) => {
                    const line = el('span', 'help-line' + (i === 3 || i === 4 ? ' help-added' : ''));
                    line.style.width = (width * 100) + '%';
                    line.style.animationDelay = (i * 70) + 'ms';
                    text.appendChild(line);
                });
                panel.appendChild(header);
                panel.appendChild(status);
                panel.appendChild(text);
                page.appendChild(panel);
                return { stage: stage, page: page, view: view, block: block, panel: panel, dot: finger(stage) };
            },
            reset(c) {
                focus(c.view, c.block);
                c.block.setAttribute('aria-pressed', 'false');
                c.page.style.transform = '';
                c.panel.classList.remove('shown', 'flash');
                hide(c.dot, rest(c.stage));
            },
            select(c) {
                c.block.setAttribute('aria-pressed', 'true');
                // The page moves up until the panel fills the lower part, the tapped cell still in sight.
                const cellTop = c.view.wrapper.offsetTop + c.block.offsetTop - c.view.wrapper.scrollTop;
                const lift = Math.max(0, Math.min(c.panel.offsetTop - c.stage.clientHeight * 0.4, cellTop - 12));
                c.page.style.transform = 'translateY(' + -lift + 'px)';
                c.panel.classList.add('shown');
            },
            async play(c, token) {
                await moveTo(c.dot, within(c.stage, c.block, 0.45, 0.55), token, 800);
                await tap(c.dot, token);
                c.block.setAttribute('aria-pressed', 'true');
                await sleep(500, token);
                hide(c.dot);
                this.select(c);
                await sleep(900, token);
                c.panel.classList.add('flash');
                await sleep(2200, token);
            },
            end(c) {
                this.select(c);
            },
        },

        filter: {
            needs: () => !!document.querySelector('.filter-list > .filter-item input[data-ai]'),
            build(stage) {
                const view = gridView(stage);
                if (!view) return null;
                const rows = Array.from(document.querySelectorAll('.filter-list > .filter-item'))
                    .filter(row => row.querySelector('input[data-ai]'));
                const aiOf = row => row.querySelector('input[data-ai]').dataset.ai;
                const listed = new Set(rows.map(aiOf));
                const block = pickBlock(view.grid, b => (b.dataset.ai || '').split('|').some(ai => listed.has(ai)));
                if (!block) return null;
                const ai = block.dataset.ai.split('|').find(v => listed.has(v));
                const index = rows.findIndex(row => aiOf(row) === ai);
                const source = document.querySelector('.filter-panel');
                const panel = copy(source);
                panel.classList.add('collapsed', 'help-filter');
                const list = panel.querySelector('.filter-list');
                list.innerHTML = '';
                const separator = document.querySelector('.filter-list > .filter-separator');
                if (separator) list.appendChild(copy(separator));
                let target = null;
                rows.slice(Math.max(0, index - 2), index + 3).forEach(row => {
                    const item = copy(row);
                    item.querySelectorAll('input').forEach(input => { input.checked = false; });
                    if (aiOf(row) === ai) target = item;
                    list.appendChild(item);
                });
                stage.appendChild(panel);
                return {
                    stage: stage, view: view, block: block, panel: panel, target: target,
                    toggle: panel.querySelector('.filter-toggle'), count: panel.querySelector('.filter-active-count'),
                    dimmed: dimmedBy(view.grid, ai), dot: finger(stage),
                };
            },
            reset(c) {
                focus(c.view, c.block);
                c.panel.classList.add('collapsed');
                c.toggle.textContent = '◀ Filter';
                c.target.querySelector('input').checked = false;
                if (c.count) c.count.textContent = '';
                c.view.grid.querySelectorAll('.session-block.dimmed').forEach(b => b.classList.remove('dimmed'));
                hide(c.dot, rest(c.stage));
            },
            check(c) {
                c.target.querySelector('input').checked = true;
                if (c.count) c.count.textContent = '1';
                c.dimmed.forEach(b => b.classList.add('dimmed'));
            },
            async play(c, token) {
                await moveTo(c.dot, within(c.stage, c.toggle, 0.5, 0.5), token, 800);
                await tap(c.dot, token);
                c.panel.classList.remove('collapsed');
                c.toggle.textContent = '▶';
                await sleep(500, token);
                await moveTo(c.dot, within(c.stage, c.target.querySelector('input'), 0.5, 0.5), token, 600);
                await tap(c.dot, token);
                this.check(c);
                await sleep(1100, token);
                await moveTo(c.dot, within(c.stage, c.toggle, 0.5, 0.5), token, 500);
                await tap(c.dot, token);
                c.panel.classList.add('collapsed');
                c.toggle.textContent = '◀ Filter';
                await sleep(300, token);
                await moveTo(c.dot, rest(c.stage), token, 500);
            },
            end(c) {
                this.check(c);
            },
        },

        pinch: {
            build(stage) {
                const view = gridView(stage);
                if (!view) return null;
                return { stage: stage, view: view, dots: [finger(stage), finger(stage)] };
            },
            // The whole day across the view, as the real pinch allows at its smallest.
            smallest(c) {
                return Math.min(1, c.view.wrapper.clientWidth / c.view.grid.offsetWidth);
            },
            fingers(c, gap) {
                const x = c.stage.clientWidth / 2;
                const y = c.stage.clientHeight * 0.55;
                return [{ x: x - gap, y: y - gap * 0.6 }, { x: x + gap, y: y + gap * 0.6 }];
            },
            scale(c, z, ms) {
                c.view.grid.style.transition = 'transform ' + ms + 'ms ease-in-out';
                c.view.grid.style.transform = z === 1 ? '' : 'scale(' + z + ')';
            },
            reset(c) {
                c.view.wrapper.scrollLeft = 0;
                c.view.wrapper.scrollTop = 0;
                this.scale(c, 1, 0);
                const at = this.fingers(c, 70);
                c.dots.forEach((dot, i) => hide(dot, at[i]));
            },
            async move(c, gap, ms, token) {
                const at = this.fingers(c, gap);
                c.dots.forEach((dot, i) => {
                    dot.style.transitionDuration = ms + 'ms, 200ms';
                    dot.classList.add('shown', 'held');
                    place(dot, at[i]);
                });
                await sleep(ms, token);
            },
            async play(c, token) {
                await this.move(c, 70, 250, token);
                this.scale(c, this.smallest(c), 1100);
                await this.move(c, 22, 1100, token);
                c.dots.forEach(dot => hide(dot));
                await sleep(1600, token);
                await this.move(c, 22, 250, token);
                this.scale(c, 1, 900);
                await this.move(c, 60, 900, token);
                // Past 100% the fingers keep going and the grid stays: the stop.
                await this.move(c, 80, 500, token);
                c.dots.forEach(dot => hide(dot));
            },
            end(c) {
                this.scale(c, this.smallest(c), 0);
            },
        },

        switch: {
            needs: () => !!document.getElementById('wg-switch'),
            build(stage) {
                const source = document.getElementById('wg-switch');
                const others = source.querySelectorAll('.wg-switch-item:not([aria-current])');
                if (!others.length) return null;
                const top = el('div', 'help-title');
                const menu = copy(source);
                menu.open = false;
                top.appendChild(menu);
                stage.appendChild(top);
                return {
                    stage: stage, menu: menu, summary: menu.querySelector('summary'),
                    item: menu.querySelectorAll('.wg-switch-item:not([aria-current])')[0], dot: finger(stage),
                };
            },
            reset(c) {
                c.menu.open = false;
                c.item.classList.remove('help-hover');
                hide(c.dot, rest(c.stage));
            },
            async play(c, token) {
                await moveTo(c.dot, within(c.stage, c.summary, 0.55, 0.6), token, 800);
                await tap(c.dot, token);
                c.menu.open = true;
                await sleep(600, token);
                await moveTo(c.dot, within(c.stage, c.item, 0.4, 0.55), token, 700);
                c.item.classList.add('help-hover');
                await sleep(1400, token);
                await moveTo(c.dot, rest(c.stage), token, 500);
            },
            end(c) {
                c.menu.open = true;
            },
        },
    };

    // Beside the cell like the real popup: right, then left, else under it.
    // A phone-wide view has no free side, so it opens under the cell and runs
    // off the bottom of the view the way it would run down the screen.
    function placePopup(stage, popup, block) {
        const margin = 8;
        const s = stage.getBoundingClientRect();
        const r = block.getBoundingClientRect();
        const cell = { left: r.left - s.left, right: r.right - s.left, top: r.top - s.top, bottom: r.bottom - s.top };
        const w = popup.offsetWidth;
        const h = popup.offsetHeight;
        let left = cell.left;
        // Under a tall cell it still starts in view.
        let top = Math.min(cell.bottom + margin, s.height * 0.55);
        const beside = cell.right + margin + w <= s.width - margin ? cell.right + margin
            : cell.left - margin - w >= margin ? cell.left - margin - w : null;
        if (beside !== null) {
            left = beside;
            top = Math.max(margin, Math.min(cell.top, s.height - margin - h));
        }
        popup.classList.toggle('help-popup-under', beside === null);
        popup.style.left = Math.max(margin, Math.min(left, s.width - margin - w)) + 'px';
        popup.style.top = top + 'px';
    }

    // The cells a filter on this one item fades: the same rule as applyFilter in schedule.js.
    function dimmedBy(grid, ai) {
        let ids = [ai];
        try {
            const data = JSON.parse(document.getElementById('filter-data').textContent);
            if (data.aiIds && data.aiIds[ai]) ids = data.aiIds[ai];
        } catch (e) {
            // the item's own number is enough
        }
        function scopeMatches(entry) {
            const covers = entry.slice(-2) === '.*';
            const id = covers ? entry.slice(0, -2) : entry;
            return ids.some(sel => sel === id || id.indexOf(sel + '.') === 0 || (covers && sel.indexOf(id + '.') === 0));
        }
        return Array.from(grid.querySelectorAll('.session-block')).filter(block => {
            const values = (block.getAttribute('data-ai') || '').split('|');
            const scope = (block.getAttribute('data-ai-scope') || '').split('|').filter(Boolean);
            return values.indexOf(ai) < 0 && !scope.some(scopeMatches);
        });
    }

    function stillMotion() {
        return window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    }

    async function loop(scene, c, token) {
        try {
            for (;;) {
                scene.reset(c);
                await sleep(500, token);
                await scene.play(c, token);
                await sleep(HOLD_MS, token);
            }
        } catch (e) {
            if (e !== STOP) throw e;
        }
    }

    function start(entry) {
        if (entry.token) return;
        entry.token = { live: true };
        loop(entry.scene, entry.c, entry.token);
    }

    function stop(entry) {
        if (!entry.token) return;
        entry.token.live = false;
        entry.token = null;
    }

    function build() {
        running = [];
        guide.querySelectorAll('.help-scene').forEach(section => {
            const scene = scenes[section.dataset.scene];
            const stage = section.querySelector('.help-stage');
            stage.innerHTML = '';
            const c = scene && (!scene.needs || scene.needs()) ? scene.build(stage) : null;
            section.hidden = !c;
            if (!c) return;
            const entry = { section: section, stage: stage, scene: scene, c: c, token: null };
            running.push(entry);
            scene.reset(c);
            // Still pictures for those who asked for less motion: each scene's last frame.
            if (stillMotion()) {
                section.classList.add('help-still');
                scene.end(c);
            }
        });
        if (stillMotion() || !('IntersectionObserver' in window)) return;
        // A scene plays while its stage is on screen, from its start each time it
        // comes back. The stage, not the whole scene: on a short screen (a phone
        // on its side) the text and stage together never fit.
        observer = new IntersectionObserver(entries => {
            entries.forEach(item => {
                const entry = running.find(r => r.stage === item.target);
                if (!entry) return;
                if (item.isIntersecting) start(entry);
                else {
                    stop(entry);
                    entry.scene.reset(entry.c);
                }
            });
        }, { root: body, threshold: 0.5 });
        running.forEach(entry => observer.observe(entry.stage));
    }

    function teardown() {
        if (observer) observer.disconnect();
        observer = null;
        running.forEach(stop);
        running = [];
        guide.querySelectorAll('.help-stage').forEach(stage => { stage.innerHTML = ''; });
    }

    // Built at every opening: the copies follow the day and the filter shown now.
    button.addEventListener('click', () => {
        guide.showModal();
        body.scrollTop = 0;
        build();
    });
    guide.querySelector('.help-guide-close').addEventListener('click', () => guide.close());
    // A click on the backdrop lands on the dialog itself; its content fills the box.
    guide.addEventListener('click', event => {
        if (event.target === guide) guide.close();
    });
    guide.addEventListener('close', () => {
        teardown();
        button.focus();
    });
})();
