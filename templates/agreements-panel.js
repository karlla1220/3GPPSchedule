// Buildless SolidJS island: only the selected AI body is fetched.
const mount = document.getElementById('agreement-app');
const manifest = JSON.parse(document.getElementById('agreement-data').textContent);
let latest = null;
let pending = null;
let updateSelection = null;

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
            ALLOWED_TAGS: ('div article p span br a img sub sup ins del ul ol li table thead tbody tr td th ' +
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
        const properties = new Set(('font-weight font-style text-decoration text-decoration-line text-decoration-style text-decoration-color text-decoration-thickness color font-size font-family background-color margin-left margin-right text-indent margin-top margin-bottom text-align width max-width height background').split(' '));
        for (const node of fragment.querySelectorAll('[style]')) {
            for (const property of [...node.style]) {
                const value = node.style.getPropertyValue(property);
                if (!properties.has(property) || /url\s*\(|expression\s*\(|[\\@]/i.test(value)) node.style.removeProperty(property);
            }
        }
        root.append(style, fragment);
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
        // Changes are dated in the meeting's time zone, like the schedule.
        const when = iso => {
            const options = { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit', hourCycle: 'h23', timeZoneName: 'short' };
            try { return new Intl.DateTimeFormat('en-GB', { ...options, timeZone: manifest.timezone || 'UTC' }).format(new Date(iso)); }
            catch { return new Intl.DateTimeFormat('en-GB', { ...options, timeZone: 'UTC' }).format(new Date(iso)); }
        };
        const version = name => (name || '').match(/\bv\d+(?:\.\d+)*\b/i)?.[0] || (name || '').replace(/\.(?:docx|docm|zip)$/i, '');
        const updatedNow = ai => {
            const section = manifest.sections[ai];
            return section && section.change !== 'initial' && section.changed_in === (manifest.source_name || manifest.document_file);
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
