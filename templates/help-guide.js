// The Help button starts a tour of the page itself: each step scrolls to a
// feature, spotlights it and says what it does in a card. Steps that show a
// gesture (tap, filter, pinch) also play it in the card, on copies of this
// page's own cells (same markup, same CSS, this meeting's sessions), so the
// tour never goes stale. The tour only points: it never taps, filters or
// zooms the page, writes nothing to storage, and puts the scroll back.
(function() {
    const button = document.getElementById('help-button');
    const guide = document.getElementById('help-guide');
    if (!button || !guide) return;
    if (typeof guide.showModal !== 'function') {
        button.hidden = true;
        return;
    }
    const STEPS = {{HELP_STEPS_JSON}};
    const spot = guide.querySelector('.help-spot');
    const card = guide.querySelector('.help-card');
    const count = guide.querySelector('.help-count');
    const title = guide.querySelector('#help-title');
    const text = guide.querySelector('#help-text');
    const stage = guide.querySelector('.help-stage');
    const back = guide.querySelector('.help-back');
    const next = guide.querySelector('.help-next');
    const STOP = {};
    // A scene holds its last frame this long before it plays again.
    const HOLD_MS = 2200;
    // Room around the spotlighted target, and between it, the card and the screen's edges.
    const PAD = 6;
    const EDGE = 8;
    const GAP = 12;
    let tour = null;

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

    // ── Scenes played in the card ──
    const scenes = {
        details: {
            // The copy of the very cell the tour spotlights, when there is one.
            build(stage, index) {
                const view = gridView(stage);
                const block = view && (view.grid.querySelectorAll('.session-block')[index] || pickBlock(view.grid));
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
        return !!(window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches);
    }

    function touchScreen() {
        return !!(window.matchMedia && window.matchMedia('(any-pointer: coarse)').matches);
    }

    function shown(node) {
        return !!node && !node.hidden && node.getClientRects().length > 0;
    }

    function viewport() {
        const v = window.visualViewport;
        return { width: v ? v.width : window.innerWidth, height: v ? v.height : window.innerHeight };
    }

    // A session in view to point at: the grid scrolls sideways in its own box,
    // and moving it would leave the visitor somewhere else.
    function visibleCell() {
        const grid = sourceGrid();
        if (!grid) return null;
        const box = grid.parentElement.getBoundingClientRect();
        const cell = pickBlock(grid, block => {
            const r = block.getBoundingClientRect();
            return r.left >= box.left && r.right <= box.right;
        });
        return cell && { el: cell, index: Array.from(grid.querySelectorAll('.session-block')).indexOf(cell) };
    }

    function target(step) {
        if (step.touch && !touchScreen()) return null;
        if (step.target === 'cell') return visibleCell();
        if (step.target === 'grid') {
            const grid = sourceGrid();
            return grid && { el: grid.parentElement };
        }
        const node = document.querySelector(step.target);
        return shown(node) ? { el: node } : null;
    }

    function fixedPosition(node) {
        for (let n = node; n && n !== document.body; n = n.parentElement) {
            if (getComputedStyle(n).position === 'fixed') return true;
        }
        return false;
    }

    // The card sits at the bottom of the screen, or at the top when it hides
    // less of the target there (a fixed target, the end of the page). Never
    // over the target's start: a tall section is read from its heading.
    function dock(rect) {
        const hidden = () => {
            const c = card.getBoundingClientRect();
            const w = Math.min(rect.right + PAD, c.right) - Math.max(rect.left - PAD, c.left);
            const h = Math.min(rect.bottom + PAD, c.bottom) - Math.max(rect.top - PAD, c.top);
            return Math.max(0, w) * Math.max(0, h);
        };
        card.classList.remove('help-card-top');
        const bottom = hidden();
        if (!bottom) return;
        card.classList.add('help-card-top');
        const top = hidden();
        const start = card.getBoundingClientRect().bottom > rect.top - PAD && top > 0 && rect.bottom - rect.top > card.offsetHeight;
        if (top >= bottom || start) card.classList.remove('help-card-top');
    }

    // Scroll so the target is in the free part of the screen above the card:
    // centred when it fits. A taller one (a section below the grid) starts a
    // quarter of the way down, so what is above it shows where it is.
    function scrollTo(node, smooth) {
        const v = viewport();
        const rect = node.getBoundingClientRect();
        const free = v.height - card.offsetHeight - EDGE - GAP;
        const top = rect.height + 2 * PAD > free - EDGE ? EDGE + PAD + Math.min(120, free / 4) : (free - rect.height) / 2;
        const max = document.documentElement.scrollHeight - v.height;
        const y = Math.max(0, Math.min(max, window.scrollY + rect.top - top));
        window.scrollTo({ top: y, behavior: smooth ? 'smooth' : 'auto' });
        return rect.top - (y - window.scrollY);
    }

    // The spotlight follows its target on every frame the page moves.
    function placeSpot() {
        if (!tour || !tour.current) return;
        const v = viewport();
        const r = tour.current.el.getBoundingClientRect();
        const top = Math.max(EDGE, r.top - PAD);
        const left = Math.max(EDGE, r.left - PAD);
        const bottom = Math.min(v.height - EDGE, r.bottom + PAD);
        const right = Math.min(v.width - EDGE, r.right + PAD);
        spot.style.transform = 'translate(' + left + 'px, ' + top + 'px)';
        spot.style.width = Math.max(0, right - left) + 'px';
        spot.style.height = Math.max(0, bottom - top) + 'px';
    }

    let frame = 0;
    function follow() {
        if (frame) return;
        frame = requestAnimationFrame(() => {
            frame = 0;
            placeSpot();
        });
    }

    function stopScene() {
        if (!tour) return;
        if (tour.token) tour.token.live = false;
        tour.token = null;
        stage.innerHTML = '';
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

    function playScene(step, found) {
        const scene = step.scene && scenes[step.scene];
        stage.hidden = !scene;
        if (!scene) return;
        // No room for it (a phone on its side): the words alone.
        if (!stage.offsetHeight) return;
        const c = scene.build(stage, found.index);
        if (!c) {
            stage.hidden = true;
            return;
        }
        scene.reset(c);
        if (stillMotion()) {
            scene.end(c);
            return;
        }
        tour.token = { live: true };
        loop(scene, c, tour.token);
    }

    function show(index, smooth) {
        stopScene();
        const step = tour.steps[index];
        tour.index = index;
        tour.current = step.found;
        count.textContent = (index + 1) + ' / ' + tour.steps.length;
        title.textContent = step.title;
        text.textContent = step.text;
        back.disabled = index === 0;
        next.textContent = index === tour.steps.length - 1 ? 'Done' : 'Next';
        playScene(step, step.found);
        const node = step.found.el;
        let rect = node.getBoundingClientRect();
        if (!fixedPosition(node)) {
            card.classList.remove('help-card-top');
            const top = scrollTo(node, smooth);
            rect = { top: top, bottom: top + rect.height, left: rect.left, right: rect.right };
        }
        dock(rect);
        placeSpot();
        next.focus({ preventScroll: true });
    }

    function start() {
        const steps = STEPS.map(step => Object.assign({ found: target(step) }, step)).filter(step => step.found);
        if (!steps.length) return;
        tour = { steps: steps, index: 0, current: null, token: null, width: viewport().width,
                 scrollX: window.scrollX, scrollY: window.scrollY };
        guide.classList.toggle('help-still', stillMotion());
        guide.showModal();
        spot.classList.add('help-spot-jump');
        show(0, false);
        // The first spotlight appears where it is; later ones glide.
        requestAnimationFrame(() => spot.classList.remove('help-spot-jump'));
        window.addEventListener('scroll', follow, true);
        window.addEventListener('resize', relayout);
        if (window.visualViewport) window.visualViewport.addEventListener('resize', relayout);
    }

    // A new width (rotation, a resized window) lays the step out again. A new
    // height alone is a phone's toolbar showing or hiding as the page scrolls:
    // the spotlight follows, the page is not scrolled back.
    function relayout() {
        if (!tour) return;
        const width = viewport().width;
        if (width === tour.width) {
            placeSpot();
            return;
        }
        tour.width = width;
        show(tour.index, false);
    }

    function finish() {
        if (!tour) return;
        stopScene();
        window.removeEventListener('scroll', follow, true);
        window.removeEventListener('resize', relayout);
        if (window.visualViewport) window.visualViewport.removeEventListener('resize', relayout);
        window.scrollTo({ left: tour.scrollX, top: tour.scrollY, behavior: 'auto' });
        tour = null;
        button.focus({ preventScroll: true });
    }

    button.addEventListener('click', start);
    next.addEventListener('click', () => {
        if (tour.index < tour.steps.length - 1) show(tour.index + 1, !stillMotion());
        else guide.close();
    });
    back.addEventListener('click', () => {
        if (tour.index > 0) show(tour.index - 1, !stillMotion());
    });
    guide.querySelector('.help-close').addEventListener('click', () => guide.close());
    guide.addEventListener('keydown', event => {
        if (!tour) return;
        if (event.key === 'ArrowRight') next.click();
        else if (event.key === 'ArrowLeft' && !back.disabled) back.click();
    });
    // A tap beside the card ends the tour; one on the spotlight does nothing,
    // since it may be a try at the feature it shows.
    guide.addEventListener('click', event => {
        if (event.target !== guide) return;
        const s = spot.getBoundingClientRect();
        const inSpot = event.clientX >= s.left && event.clientX <= s.right && event.clientY >= s.top && event.clientY <= s.bottom;
        if (!inSpot) guide.close();
    });
    // Esc (the dialog's cancel) and every other way out end here.
    guide.addEventListener('close', finish);
})();
