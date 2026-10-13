// Buildless SolidJS island: only the selected AI body is fetched.
const mount = document.getElementById('agreement-app');
const manifest = JSON.parse(document.getElementById('agreement-data').textContent);
let latest = null;
let pending = null;
let updateSelection = null;

// Changes are dated in the meeting's time zone, like the schedule.
const when = iso => {
    const options = { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit', hourCycle: 'h23', timeZoneName: 'short' };
    try { return new Intl.DateTimeFormat('en-GB', { ...options, timeZone: manifest.timezone || 'UTC' }).format(new Date(iso)); }
    catch { return new Intl.DateTimeFormat('en-GB', { ...options, timeZone: 'UTC' }).format(new Date(iso)); }
};
// "_v01" counts ("_" is not a boundary for \b). A draft says so: the chair
// restarts at v00 when the draft is released.
const version = name => {
    const found = (name || '').match(/(?:^|[^a-z0-9])(v\d+(?:\.\d+)*)(?![a-z0-9])/i)?.[1];
    if (!found) return (name || '').replace(/\.(?:docx|docm|zip)$/i, '');
    return (/(?:^|[^a-z0-9])draft(?![a-z0-9])/i.test(name) ? 'draft ' : '') + found;
};
// The note the panel was built from. A section's own line names the note that
// last changed it, which says nothing about how far the panel has read.
const noteFile = manifest.source_name || manifest.document_file || '';
const sourceNote = () => {
    if (manifest.status !== 'ready' || !noteFile) return null;
    const line = document.createElement('p');
    line.className = 'agreement-source';
    line.title = noteFile;
    const shown = version(noteFile);
    line.textContent = (/^(?:draft )?v\d/.test(shown) ? 'Chair notes ' : '') + shown
        + (manifest.document_changed_at ? ' · ' + when(manifest.document_changed_at) : '');
    return line;
};
// Shown before any cell is selected: the viewer loads on the first selection.
{
    const line = sourceNote();
    if (line) mount.querySelector('.agreement-header')?.append(line);
}

// Added blocks in a row read as one change: each bar reaches up to the
// previous added block across margins and blank paragraphs, so the margin
// shows one line instead of a dash per paragraph. Anything else in between
// (text, an image, a folded TDoc list) keeps the bars apart.
const joinAdded = root => {
    const added = [...root.querySelectorAll('.agreement-added')];
    const joined = added.slice(1).map((block, i) => [added[i], block]).filter(([above, block]) => {
        if (above.contains(block)) return false;
        const between = document.createRange();
        between.setStartAfter(above);
        between.setEndBefore(block);
        return !between.toString().trim() && !between.cloneContents().querySelector('img, table, math');
    });
    if (!joined.length) return;
    // The gap depends on fonts and width, so it is measured whenever the document resizes.
    new ResizeObserver(() => {
        for (const [above, block] of joined) {
            const gap = block.getBoundingClientRect().top - above.getBoundingClientRect().bottom;
            block.style.setProperty('--added-reach', Math.max(0, gap) + 'px');
        }
    }).observe(root.querySelector('.docx-document') || root.host);
};

// A table keeps Word's line breaks: they are laid out at Word's column widths
// (the table's min-width). Small text can be zoomed, a reflowed grid cannot.
// When the panel is narrower, each column gives up the room its lines do not
// use, and the table is scaled (CSS zoom) to the largest size that fits with
// a fixed on-screen cell padding. Only the outermost table is fitted.
const FIT_PAD = { x: 4, y: 2 };
// Text nodes in document order, plus boxes a line must hold whole (an
// equation is one box: its scripts sit at other heights than its line).
const pieces = cell => {
    const found = [];
    const walker = document.createTreeWalker(cell, NodeFilter.SHOW_ELEMENT | NodeFilter.SHOW_TEXT);
    for (let node = walker.nextNode(); node; node = walker.nextNode()) {
        if (node.nodeType === Node.TEXT_NODE ? node.data.trim() && !node.parentElement.closest('math') : node.matches('img, table, math')) found.push(node);
    }
    return found;
};
const rectsOf = node => {
    if (node.nodeType !== Node.TEXT_NODE) return [...node.getClientRects()];
    const range = document.createRange();
    range.selectNodeContents(node);
    return [...range.getClientRects()].filter(rect => rect.width > 0);
};
// Lines per cell: text boxes that overlap vertically share a line. Equal
// counts mean the same line breaks; a wider column never needs more lines.
const lineCounts = table => [...table.querySelectorAll('td, th')].map(cell => {
    const boxes = pieces(cell).filter(node => !node.matches?.('table')).flatMap(rectsOf).sort((a, b) => a.top - b.top);
    let lines = 0, bottom = -Infinity;
    for (const box of boxes) {
        if (box.top >= bottom - 1) { lines++; bottom = box.bottom; } else bottom = Math.max(bottom, box.bottom);
    }
    return lines;
});
// First grid column and span of every cell, through rowspans.
const gridCells = table => {
    const taken = [];
    return [...table.rows].flatMap((row, r) => {
        let column = 0;
        return [...row.cells].map(cell => {
            while (taken[r]?.[column]) column++;
            for (let i = 0; i < cell.rowSpan; i++) for (let j = 0; j < cell.colSpan; j++) (taken[r + i] ??= [])[column + j] = true;
            const placed = { cell, start: column, span: cell.colSpan };
            column += cell.colSpan;
            return placed;
        });
    });
};
const wordWidths = new WeakMap();
const fitTable = table => {
    const cols = [...table.querySelectorAll(':scope > colgroup > col')];
    if (!wordWidths.has(table)) wordWidths.set(table, cols.map(col => col.style.width));
    table.classList.remove('fitted');
    table.style.zoom = table.style.width = '';
    cols.forEach((col, i) => { col.style.width = wordWidths.get(table)[i]; });
    const room = table.parentElement.clientWidth;
    if (!room || table.offsetWidth <= room) return;

    // Widest line of each cell at Word's widths, left-aligned: justified and
    // centred lines take more room than their words need.
    table.classList.add('measuring');
    const need = cols.map(() => 0);
    const word = cols.map(() => 0);
    const cells = gridCells(table).map(placed => {
        const box = placed.cell.getBoundingClientRect();
        const style = getComputedStyle(placed.cell);
        const left = box.left + parseFloat(style.borderLeftWidth) + parseFloat(style.paddingLeft);
        // What overflows at Word's width overflows anyway: a wider column would reflow it.
        const inner = placed.cell.clientWidth - parseFloat(style.paddingLeft) - parseFloat(style.paddingRight);
        const right = Math.min(inner, Math.max(0, ...pieces(placed.cell).flatMap(rectsOf).map(rect => rect.right - left)));
        return { ...placed, right, inner, edge: box.width - inner };
    });
    const before = lineCounts(table);
    table.classList.remove('measuring');
    for (const { start, span, right, inner } of cells) {
        if (span !== 1 || start >= need.length) continue;
        need[start] = Math.max(need[start], right);
        word[start] = inner;
    }
    // A spanning cell widens its columns by what they lack together, from the
    // room each has at Word's width, so none grows past it.
    for (const { start, span, right, edge } of cells) {
        if (span === 1) continue;
        const columns = need.slice(start, start + span).map((width, i) => [start + i, width]);
        const lack = right - columns.reduce((sum, [, width]) => sum + width, 0) - (span - 1) * edge;
        const spare = columns.map(([c, width]) => Math.max(0, word[c] - width));
        const free = spare.reduce((a, b) => a + b, 0);
        if (lack <= 0) continue;
        columns.forEach(([c], i) => { need[c] += lack * (free ? spare[i] / free : 1 / span); });
    }

    // Chrome raises text below its minimum font size, so a small enough scale
    // wraps lines again. Then the table keeps the smallest scale that holds
    // the lines and scrolls sideways (inside .table-pan, which leaves the
    // added-part bar on .table-scroll unclipped).
    const words = need.reduce((a, b) => a + b * 1.01 + 1, 0);
    const frame = cols.length * (2 * FIT_PAD.x + 1) + 1;
    for (let zoom = Math.min(1, (room - frame) / words); zoom > 0; zoom = Math.min(1, zoom * 1.08)) {
        // Padding is set before zoom, so it stays the same on screen.
        table.style.setProperty('--fit-pad-x', FIT_PAD.x / zoom + 'px');
        table.style.setProperty('--fit-pad-y', FIT_PAD.y / zoom + 'px');
        const widths = need.map(width => width * 1.01 + 1 + (2 * FIT_PAD.x + 1) / zoom);
        cols.forEach((col, i) => { col.style.width = widths[i] + 'px'; });
        table.style.width = zoom < 1 ? widths.reduce((a, b) => a + b, 0) + 'px' : '';
        table.style.zoom = zoom < 1 ? zoom.toFixed(4) : '';
        table.classList.add('fitted');
        // Joined lines are fine (the column has room to spare); more lines are not.
        if (zoom === 1 || lineCounts(table).every((count, i) => count <= before[i])) return;
    }
};
const fitTables = root => {
    const tables = [...root.querySelectorAll('table.word-grid')].filter(table => !table.parentElement.closest('table'));
    if (!tables.length || !CSS.supports('zoom', '1')) return;
    for (const table of tables) {
        const pan = document.createElement('div');
        pan.className = 'table-pan';
        table.replaceWith(pan);
        pan.append(table);
    }
    let width = null;
    // Line widths are measured, so the web font must be in place first.
    document.fonts.ready.then(() => new ResizeObserver(([entry]) => {
        // Scaling changes only the height; refit when the width changes.
        if (!entry.contentRect.width || entry.contentRect.width === width) return;
        width = entry.contentRect.width;
        tables.forEach(fitTable);
    }).observe(root.querySelector('.docx-document') || root.host));
};

async function start() {
    const [{ createSignal, createResource, batch, For, Show, onCleanup }, { render }, { default: html }, { default: DOMPurify }] =
        await Promise.all([import('solid-js'), import('solid-js/web'), import('solid-js/html'), import('dompurify')]);
    const documentView = content => {
        const host = document.createElement('div');
        host.className = 'agreement-document';
        // Same-page content with natural height. Shadow DOM isolates Word CSS;
        // unlike an iframe, it does not create another viewport or scroll area.
        const root = host.attachShadow({ mode: 'open' });
        const style = document.createElement('style');
        style.textContent = manifest.document_css;
        const fragment = DOMPurify.sanitize(content, {
            RETURN_DOM_FRAGMENT: true,
            ALLOWED_TAGS: ('div article p span br a img sub sup ins del ul ol li table colgroup col thead tbody tr td th details summary ' +
                'math mrow mi mn mo mtext mspace ms mfrac msqrt mroot mstyle merror mpadded mphantom mfenced menclose msub msup msubsup munder mover munderover mmultiscripts mprescripts none mtable mtr mtd mlabeledtr').split(' '),
            FORBID_ATTR: ['id', 'name'],
            ALLOW_DATA_ATTR: false,
        });
        // Keep only locally embedded images and passive formatting attributes.
        for (const image of fragment.querySelectorAll('img')) {
            if (!/^data:image\/(?:png|jpeg|gif|svg\+xml);base64,[A-Za-z0-9+/=\s]+$/.test(image.getAttribute('src') || '')) image.remove();
            image.removeAttribute('srcset');
        }
        for (const link of fragment.querySelectorAll('a')) {
            if (!/^https?:\/\//i.test(link.getAttribute('href') || '')) link.removeAttribute('href');
            link.setAttribute('target', '_blank');
            link.setAttribute('rel', 'noopener noreferrer');
        }
        // node.style lists longhands: text-decoration arrives as text-decoration-line etc.
        const properties = new Set(('font-weight font-style text-decoration text-decoration-line text-decoration-style text-decoration-color text-decoration-thickness color font-size font-family background-color margin-left margin-right text-indent margin-top margin-bottom text-align width min-width max-width height background').split(' '));
        for (const node of fragment.querySelectorAll('[style]')) {
            for (const property of [...node.style]) {
                const value = node.style.getPropertyValue(property);
                if (!properties.has(property) || /url\s*\(|expression\s*\(|[\\@]/i.test(value)) node.style.removeProperty(property);
            }
        }
        root.append(style, fragment);
        joinAdded(root);
        fitTables(root);
        return host;
    };
    mount.replaceChildren();
    render(() => {
        const [selection, setSelection] = createSignal(latest);
        // Open on the first item with text; empty items keep their tabs.
        const firstAI = ais => ais.find(ai => manifest.sections[ai]?.url) || ais[0] || null;
        const [activeAI, setActiveAI] = createSignal(latest ? firstAI(latest.ais) : null);
        const cache = new Map();
        let controller;
        updateSelection = chosen => batch(() => {
            if (!chosen) controller?.abort();
            setSelection(chosen);
            setActiveAI(chosen?.ais.includes(activeAI()) ? activeAI() : chosen ? firstAI(chosen.ais) : null);
        });
        const entry = () => Object.hasOwn(manifest.sections, activeAI()) ? manifest.sections[activeAI()] : null;
        // An item without text is normal (a heading over subsections, or nothing agreed yet).
        const emptyNote = () => {
            const ai = activeAI();
            if (!entry()) return 'AI ' + ai + ' is not a heading in the chair notes.';
            if (Object.keys(manifest.sections).some(other => other.startsWith(ai + '.'))) return 'Nothing is recorded directly under this item; see its subsections.';
            if (entry().excluded_tdoc_rows) return 'Only TDoc listings are recorded under this item.';
            return 'Nothing is recorded under this item yet.';
        };
        const [document, { refetch }] = createResource(activeAI, async ai => {
            controller?.abort();
            controller = new AbortController();
            const signal = controller.signal;
            const current = manifest.sections[ai];
            if (manifest.status !== 'ready' || !current?.url) return {};
            if (!/^\.\/agreements\/[a-f0-9]{20}\.html$/.test(current.url)) return { error: true };
            try {
                let content = cache.get(current.url);
                if (!content) {
                    const response = await fetch(current.url, { signal, credentials: 'same-origin' });
                    if (!response.ok) throw new Error('HTTP ' + response.status);
                    content = await response.text();
                    cache.set(current.url, content);
                }
                return { content };
            } catch { return { error: true }; }
        });
        onCleanup(() => controller?.abort());
        const tabID = ai => 'agreement-tab-' + ai.replaceAll('.', '-');
        const updatedNow = ai => {
            const section = manifest.sections[ai];
            return section && section.change !== 'initial' && section.changed_in === noteFile;
        };
        const changeNote = () => {
            const section = entry();
            if (!section?.changed_at) return '';
            const source = version(section.changed_in);
            if (section.change === 'initial') return 'As of ' + when(section.changed_at) + (source ? ' · ' + source : '');
            const label = section.change === 'new' ? 'Added ' : 'Updated ';
            const parts = section.added ? ' · ' + section.added + (section.added === 1 ? ' added part' : ' added parts') + ' marked' : '';
            return label + when(section.changed_at) + (source ? ' · ' + source : '') + parts;
        };
        const keyTab = (event, ai) => {
            const ais = selection().ais;
            const index = ais.indexOf(ai);
            const next = { ArrowRight: (index + 1) % ais.length, ArrowLeft: (index + ais.length - 1) % ais.length, Home: 0, End: ais.length - 1 }[event.key];
            if (next === undefined) return;
            event.preventDefault();
            setActiveAI(ais[next]);
            window.document.getElementById(tabID(ais[next]))?.focus({ preventScroll: true });
        };
        return html`
            <div>
                <div class="agreement-header">
                    <h2 id="agreement-title" title=${manifest.document_file ? manifest.document_file + ' · SHA-256 ' + manifest.sha256 : ''}>Agreements for</h2>
                    <${Show} when=${() => selection()?.ais.length}>
                        <div class="agreement-tabs" role="tablist" aria-label="Agreement agenda items">
                            <${For} each=${() => selection().ais}>${ai => html`
                                <button type="button" role="tab" id=${tabID(ai)} aria-controls="agreement-body"
                                    aria-selected=${() => activeAI() === ai ? 'true' : 'false'} tabindex=${() => activeAI() === ai ? 0 : -1}
                                    onClick=${() => setActiveAI(ai)} onKeyDown=${event => keyTab(event, ai)}>AI ${ai}<${Show} when=${() => updatedNow(ai)}><span class="agreement-updated" title="Updated in the latest chair note"></span><//></button>`}<//>
                        </div>
                    <//>
                    ${sourceNote}
                </div>
                <p class="agreement-status" role="status" aria-live="polite">${() => !selection() ? 'Select a schedule cell to read its agreements here.' : !selection().ais.length ? 'This cell has no agenda number.' : manifest.status !== 'ready' ? 'No chairman note is available for this meeting.' : document.loading ? 'Loading agreements…' : ''}</p>
                <${Show} when=${() => activeAI() && manifest.status === 'ready'}>
                    <section id="agreement-body" role="tabpanel" aria-labelledby=${() => tabID(activeAI())} aria-busy=${() => document.loading ? 'true' : 'false'}>
                        <h3><span class="agreement-ai">${() => 'AI ' + activeAI()}</span>${' '}<span>${() => entry()?.title || ''}</span></h3>
                        <${Show} when=${changeNote}><p class=${() => 'agreement-change' + (entry()?.added ? ' has-added' : '')}>${changeNote}</p><//>
                        <${Show} when=${() => !document.loading}>
                            <${Show} when=${() => document()?.error}><p>Could not load this agreement. <button type="button" class="agreement-action" onClick=${() => refetch()}>Retry</button></p><//>
                            <${Show} when=${() => !document()?.error && !document()?.content}><p class="agreement-note">${emptyNote}</p><//>
                            <${Show} when=${() => document()?.content} keyed=${true}>${content => documentView(content)}<//>
                        <//>
                    </section>
                <//>
            </div>`;
    }, mount);
}

window.addEventListener('agreement-select', event => {
    latest = event.detail;
    if (updateSelection) { updateSelection(latest); return; }
    if (!latest || pending) return;
    const loading = document.createElement('p');
    loading.className = 'agreement-status';
    loading.textContent = 'Loading agreement viewer…';
    mount.replaceChildren(loading);
    pending = start().catch(() => {
        mount.replaceChildren();
        const message = document.createElement('p');
        message.className = 'agreement-status';
        message.textContent = 'Could not load the agreement viewer from the CDN.';
        const retry = document.createElement('button');
        retry.type = 'button';
        retry.className = 'agreement-action';
        retry.textContent = 'Reload viewer';
        retry.addEventListener('click', () => location.reload());
        mount.append(message, retry);
        updateSelection = null;
        pending = null;
    });
});
