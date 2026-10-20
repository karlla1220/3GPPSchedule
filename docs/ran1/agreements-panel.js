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
// restarts at v00 when the draft is released. The closing notes come after
// the last version and count on their own: "Final", "EOM", "EOM1" ("eom_01").
const version = name => {
    const found = (name || '').match(/(?:^|[^a-z0-9])(v\d+(?:\.\d+)*)(?![a-z0-9])/i)?.[1];
    const closing = (name || '').match(/(?:^|[^a-z0-9])(eom|final)(?:_?(\d+))?(?![a-z0-9])/i);
    if (!found && !closing) return (name || '').replace(/\.(?:docx|docm|zip)$/i, '');
    const stage = closing
        ? (closing[1].toLowerCase() === 'eom' ? 'EOM' : 'Final') + (closing[2] ? Number(closing[2]) : '')
        : '';
    return (/(?:^|[^a-z0-9])draft(?![a-z0-9])/i.test(name) ? 'draft ' : '')
        + [stage, found || ''].filter(Boolean).join(' ');
};
// Which note a version belongs to, in the source line and each item's change
// line alike: "v05" alone does not say whose note it is.
const noteLabel = name => {
    const shown = version(name);
    if (/session\s*notes?/i.test(name || '')) return 'Session notes ' + shown;
    return (/^(?:draft )?(?:v\d|EOM|Final)/.test(shown) ? 'Chair notes ' : '') + shown;
};
// The notes the panel was built from: the chairman note, and the selected
// item's session notes. A section's own line names the note that last
// changed it, which says nothing about how far the panel has read.
const noteFile = manifest.source_name || manifest.document_file || '';
const sessionNotes = manifest.session_notes || {};
const latestNotes = new Set([noteFile, ...Object.values(sessionNotes).map(note => note.name)].filter(Boolean));
const sourceNote = section => {
    const held = (section?.notes || []).map(id => sessionNotes[id]).filter(Boolean);
    if (manifest.status !== 'ready' || (!noteFile && !held.length)) return null;
    const line = document.createElement('p');
    line.className = 'agreement-source';
    const parts = [];
    if (noteFile) {
        parts.push([noteFile, noteLabel(noteFile)
            + (manifest.document_changed_at ? ' · ' + when(manifest.document_changed_at) : '')]);
    }
    for (const note of held) {
        parts.push([note.name, noteLabel(note.name) + (note.changed_at ? ' · ' + when(note.changed_at) : '')]);
    }
    parts.forEach(([name, text], i) => {
        if (i) line.append(' + ');
        const part = document.createElement('span');
        part.title = name;
        part.textContent = text;
        line.append(part);
    });
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
const fitTable = table => {
    const cols = [...table.querySelectorAll(':scope > colgroup > col')];
    // Word's widths stay on each column, for refits and for copying.
    for (const col of cols) col.dataset.word ??= col.style.width;
    table.classList.remove('fitted');
    table.style.zoom = table.style.width = '';
    cols.forEach(col => { col.style.width = col.dataset.word; });
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

// A copied selection keeps the document's structure: Markdown as plain text
// (list numbers and nesting survive in a chat or an editor) and HTML with the
// screen's formatting written inline (the document CSS stays in the Shadow
// DOM) for Word and mail. Deleted text is copied struck through.
const selectedRange = root => {
    const selection = document.getSelection();
    let found = null;
    if (selection?.getComposedRanges) {
        // Safari 17 takes the shadow roots as arguments, not in a dictionary.
        try { [found] = selection.getComposedRanges({ shadowRoots: [root] }); }
        catch { try { [found] = selection.getComposedRanges(root); } catch { found = null; } }
    } else if (root.getSelection) {
        const inner = root.getSelection();
        found = inner?.rangeCount ? inner.getRangeAt(0) : null;
    } else if (selection?.rangeCount) found = selection.getRangeAt(0);
    if (!found || found.collapsed) return null;
    const range = document.createRange();
    range.setStart(found.startContainer, found.startOffset);
    range.setEnd(found.endContainer, found.endOffset);
    return range;
};
// The selection with the elements around it: within one paragraph only its
// runs (a word in a list item is not a list item), across paragraphs the
// enclosing lists and table up to the table cell, so numbers and depth hold.
const selectedContent = (range, doc) => {
    let common = range.commonAncestorContainer;
    if (common.nodeType !== Node.ELEMENT_NODE) common = common.parentElement;
    const stop = common.closest('p, summary') || common.closest('td, th') || doc;
    let content = range.cloneContents();
    for (let node = common; node !== stop; node = node.parentElement) {
        const shell = node.cloneNode(false);
        shell.append(content);
        content = shell;
    }
    const box = document.createElement('div');
    box.append(content);
    return { box, inline: stop.matches('p, summary') };
};

// Struck through: a deletion, or a strike-through anywhere above the run.
// Bold and italics are left out: they do not change what the text says.
const struck = node => {
    for (let el = node; el; el = el.parentElement) {
        if (el.localName === 'del' || el.style?.textDecorationLine.includes('line-through')) return true;
    }
    return false;
};
// Only what would change the reading: markers, code and HTML tags
// ("<unchanged text omitted>" would vanish as a tag).
const escapeMarkdown = text => text.replace(/[\\`*~]/g, '\\$&').replace(/<(?=[a-z/!?])/gi, '\\<');
// TeX between dollars; a formula without its TeX (an older build) as its text.
const texOf = (math, open = '$', close = open) => {
    const tex = math.getAttribute('alttext')?.trim();
    return tex ? open + tex + close : math.textContent.trim();
};
// Runs of one paragraph. A strike-through opens and closes where it starts
// and ends, outside the surrounding spaces, so split Word runs do not each
// get markers.
const inlineMarkdown = nodes => {
    const pieces = [];
    const walk = nodes => {
        for (const node of nodes) {
            if (node.nodeType === Node.TEXT_NODE) {
                if (node.data) pieces.push({ text: escapeMarkdown(node.data), struck: struck(node.parentElement) });
                continue;
            }
            if (node.nodeType !== Node.ELEMENT_NODE) continue;
            const name = node.localName;
            if (name === 'br') pieces.push({ text: '\n' });
            else if (name === 'img') pieces.push({ text: '[Figure]' });
            else if (name === 'math') pieces.push({ text: texOf(node) });
            else if (name === 'sup' || name === 'sub') {
                const text = node.textContent.trim();
                if (!text) continue;
                const mark = name === 'sup' ? '^' : '_';
                pieces.push({ text: mark + escapeMarkdown(/^[\w.+\-−]+$/.test(text) ? text : '(' + text + ')'), struck: struck(node) });
            } else if (name === 'a' && node.getAttribute('href')) {
                const href = node.getAttribute('href');
                const text = node.textContent.trim();
                const url = href.replace(/[()\s]/g, encodeURIComponent);
                pieces.push({ text: !text || text === href ? url : '[' + escapeMarkdown(text) + '](' + url + ')', struck: struck(node) });
            } else walk(node.childNodes);
        }
    };
    walk(nodes);
    let out = '', open = false;
    const close = () => {
        const trail = out.match(/\s*$/)[0];
        out = out.slice(0, out.length - trail.length) + '~~' + trail;
        open = false;
    };
    for (const piece of pieces) {
        // Spaces, breaks and equations stay inside or outside as they are.
        if (piece.struck === undefined || !piece.text.trim()) { out += piece.text; continue; }
        if (open && !piece.struck) close();
        const lead = piece.text.match(/^\s*/)[0];
        out += lead + (piece.struck && !open ? '~~' : '') + piece.text.slice(lead.length);
        open = piece.struck;
    }
    if (open) close();
    out = out.trim().replace(/[ \t]*\n[ \t]*/g, '  \n');
    // A paragraph opening with # or > would turn into a heading or a quote.
    return /^[#>]/.test(out) ? '\\' + out : out;
};

const BLOCKS = new Set(['p', 'ul', 'ol', 'li', 'table', 'tbody', 'thead', 'tr', 'td', 'th', 'div', 'article', 'details', 'summary']);
// One string per Markdown block; loose runs between blocks form a paragraph.
const markdownBlocks = parent => {
    const blocks = [];
    let loose = [], articles = 0;
    const flush = () => {
        const text = inlineMarkdown(loose);
        if (text) blocks.push(text);
        loose = [];
    };
    for (const node of parent.childNodes) {
        const name = node.nodeType === Node.ELEMENT_NODE ? node.localName : '';
        if (!BLOCKS.has(name)) { loose.push(node); continue; }
        flush();
        if (name === 'article') {
            const inner = markdownBlocks(node);
            if (inner.length && articles++) blocks.push('---');
            blocks.push(...inner);
        } else blocks.push(...blockMarkdown(node));
    }
    flush();
    return blocks;
};
const blockMarkdown = node => {
    const name = node.localName;
    if (name === 'p') return markdownBlocks(node);
    if (name === 'ul' || name === 'ol') return [listMarkdown(node)].filter(Boolean);
    if (name === 'table') return [tableMarkdown(node)].filter(Boolean);
    // The TDoc list control is not text; a folded list is not on screen.
    if (name === 'summary') return [];
    // An opened TDoc list is one row a line, as on screen.
    if (name === 'details') return node.open ? [markdownBlocks(node).join('  \n')].filter(Boolean) : [];
    if (node.classList.contains('equation-block')) {
        // A table cell holds one line: its formulas stay inline.
        const [open, close] = node.closest('td, th') ? ['$', '$'] : ['$$\n', '\n$$'];
        return [...node.querySelectorAll('math')].map(math => texOf(math, open, close));
    }
    return markdownBlocks(node);
};
// Word's numbers ("value"), and each sublist under its item's text.
const listMarkdown = list => {
    const items = [];
    let number = +(list.getAttribute('start') || 1);
    for (const item of list.children) {
        if (item.localName !== 'li') continue;
        if (item.hasAttribute('value')) number = +item.getAttribute('value');
        // A gap stands for a missing parent level: indented, no marker.
        const marker = item.classList.contains('list-gap') ? '' : list.localName === 'ol' ? number++ + '. ' : '- ';
        const body = markdownBlocks(item).join('\n');
        if (!body) continue;
        const pad = ' '.repeat(marker.length || 2);
        items.push(body.split('\n').map((line, i) => (i ? (line ? pad : '') : marker || pad) + line).join('\n'));
    }
    return items.join('\n');
};
// GFM table; a merged cell keeps its text in its first slot. A one-column
// table is a box of prose (an IPR notice), so it becomes a quote.
const tableMarkdown = table => {
    const placed = gridCells(table);
    if (!placed.length) return '';
    const rows = [...table.rows];
    const width = Math.max(...placed.map(({ start, span }) => start + span));
    const cellBlocks = cell => markdownBlocks(cell);
    if (width === 1) {
        const text = placed.flatMap(({ cell }) => cellBlocks(cell)).join('\n\n');
        return text && text.split('\n').map(line => line ? '> ' + line : '>').join('\n');
    }
    const grid = rows.map(() => Array(width).fill(''));
    for (const { cell, start } of placed) {
        const r = rows.indexOf(cell.parentElement);
        grid[r][start] = cellBlocks(cell).join('<br>').replace(/ *\n */g, '<br>').replace(/\|/g, '\\|');
    }
    if (grid.every(row => row.every(text => !text))) return '';
    const line = cells => '| ' + cells.join(' | ') + ' |';
    return [line(grid[0]), line(grid[0].map(() => '---')), ...grid.slice(1).map(line)].join('\n');
};

// Inline styles for what the document CSS draws, so Word and mail show it.
const COPY_STYLE = {
    p: 'margin:0 0 .6em',
    ul: 'margin:.4em 0;padding-left:1.6em',
    ol: 'margin:.4em 0;padding-left:1.6em',
    table: 'border-collapse:collapse;width:100%;font-size:.93em;margin:12px 0',
    td: 'border:1px solid #c8ccd4;padding:4px 6px;vertical-align:top',
    th: 'border:1px solid #c8ccd4;padding:4px 6px;vertical-align:top;font-weight:600',
    ins: 'color:#17784a;text-decoration:underline',
    del: 'color:#b42318;text-decoration:line-through',
    a: 'color:#275d8c',
};
const htmlCopy = ({ box, inline }) => {
    for (const details of box.querySelectorAll('details')) {
        if (!details.open) { details.remove(); continue; }
        for (const p of details.querySelectorAll('p')) p.setAttribute('style', 'margin:0 0 .2em 1.15em;font-size:.93em;color:#475569;' + (p.getAttribute('style') || ''));
        details.querySelector(':scope > summary')?.remove();
        details.replaceWith(...details.childNodes);
    }
    // The table's on-screen fit goes; Word lays it out at its own widths.
    for (const table of box.querySelectorAll('table')) table.removeAttribute('style');
    for (const col of box.querySelectorAll('col')) {
        if (col.dataset.word) col.style.width = col.dataset.word;
        col.removeAttribute('data-word');
    }
    for (const el of box.querySelectorAll('ins *, del *')) el.style?.removeProperty('color');
    for (const el of box.querySelectorAll('*')) {
        if (el.style) for (const property of [...el.style]) if (property.startsWith('--')) el.style.removeProperty(property);
        const name = el.localName;
        let base = COPY_STYLE[name] || '';
        if (name === 'p' && el.parentElement?.localName === 'li') base = 'margin:0';
        if ((name === 'ul' || name === 'ol') && el.parentElement?.localName === 'li') base += ';margin:0';
        if (name === 'li' && el.classList.contains('list-gap')) base = 'list-style-type:none';
        if (el.classList.contains('equation-block')) base = 'margin:8px 0';
        if (base) el.setAttribute('style', base + ';' + (el.getAttribute('style') || ''));
        if (el.getAttribute('colspan') === '1') el.removeAttribute('colspan');
        if (el.getAttribute('rowspan') === '1') el.removeAttribute('rowspan');
        el.removeAttribute('class');
    }
    // Word's own size: run sizes are em of its 11pt body text.
    const tag = inline ? 'span' : 'div';
    return `<${tag} style="font-family:'Times New Roman',Times,serif;font-size:11pt">${box.innerHTML}</${tag}>`;
};

document.addEventListener('copy', event => {
    const root = mount.querySelector('.agreement-document')?.shadowRoot;
    const doc = root?.querySelector('.docx-document');
    const range = doc && selectedRange(root);
    // A selection reaching outside the document copies as the browser does.
    if (!range || !doc.contains(range.startContainer) || !doc.contains(range.endContainer) || !event.clipboardData) return;
    const content = selectedContent(range, doc);
    const markdown = markdownBlocks(content.box).join('\n\n');
    if (!markdown) return;
    event.clipboardData.setData('text/plain', markdown);
    event.clipboardData.setData('text/html', htmlCopy(content));
    event.preventDefault();
});

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
            // A formula's TeX, for copying it as Markdown.
            ADD_ATTR: ['alttext'],
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
            return section && section.change !== 'initial' && latestNotes.has(section.changed_in);
        };
        const changeNote = () => {
            const section = entry();
            if (!section?.changed_at) return '';
            const source = noteLabel(section.changed_in);
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
                                    onClick=${() => setActiveAI(ai)} onKeyDown=${event => keyTab(event, ai)}>AI ${ai}<${Show} when=${() => updatedNow(ai)}><span class="agreement-updated" title="Updated in the latest note"></span><//></button>`}<//>
                        </div>
                    <//>
                    ${() => sourceNote(entry())}
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
