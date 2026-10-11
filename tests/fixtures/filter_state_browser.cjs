// Execute the generated page script with the DOM the filter panel uses,
// a URL and a localStorage. No network or third-party browser packages.
const fs = require('node:fs');
const vm = require('node:vm');
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const stored = new Map(Object.entries(input.stored ?? {}));
const listeners = {};
const inputs = [];
function element(tag) {
    const el = {
        dataset: {}, attrs: {}, handlers: {},
        style: {setProperty() {}},
        classList: {add() {}, remove() {}, toggle: () => false},
        setAttribute(k, v) { this.attrs[k] = v; },
        addEventListener(k, fn) { this.handlers[k] = fn; },
        appendChild() {},
        querySelector: () => null,
        querySelectorAll: () => [],
    };
    if (tag === 'input') inputs.push(el);
    return el;
}
const byId = new Map(['popup-backdrop', 'popup-close-btn', 'popup-floating', 'popup-content']
    .map(id => [id, element()]));
byId.set('filter-data', {textContent: JSON.stringify({groups: [], allAIs: input.ais})});
const bySelector = new Map(['.filter-panel', '.filter-toggle', '.filter-clear', '.filter-list',
    '.filter-active-count', '.filter-dim-opacity-range', '.filter-dim-opacity-value']
    .map(selector => [selector, element()]));
const location = {pathname: '/ran1/', search: '', hash: input.hash ?? ''};
function storage(fn) {
    return (...args) => { if (input.storageError) throw Error('disabled'); return fn(...args); };
}
const context = {
    Date, Intl, Number, Math, JSON, Set, String, isFinite, encodeURIComponent, decodeURIComponent,
    location,
    history: {replaceState(state, title, url) { location.hash = url.startsWith('#') ? url : ''; }},
    document: {
        getElementById: id => byId.get(id) ?? null,
        querySelector: selector => bySelector.get(selector) ?? null,
        querySelectorAll: selector => selector === 'input[data-ai]' ? inputs.filter(el => 'ai' in el.dataset) : [],
        createElement: element,
        addEventListener(event, fn) { listeners[event] = fn; },
        documentElement: {clientWidth: 1440, clientHeight: 900, style: {setProperty() {}}},
    },
    window: {addEventListener() {}},
    localStorage: {
        getItem: storage(k => stored.get(k) ?? null),
        setItem: storage((k, v) => { stored.set(k, v); }),
        removeItem: storage(k => { stored.delete(k); }),
    },
    sessionStorage: {getItem: () => null, setItem() {}},
    setInterval() {}, setTimeout: () => 0, clearTimeout() {},
};
vm.runInNewContext(input.script, context);
listeners.DOMContentLoaded();
function state() {
    return {hash: location.hash, stored: Object.fromEntries(stored),
            checked: inputs.filter(el => el.checked).map(el => el.dataset.ai)};
}
const states = [state()];
for (const step of input.steps ?? []) {
    if (step.clear) bySelector.get('.filter-clear').handlers.click();
    else {
        const box = inputs.find(el => el.dataset.ai === step.toggle);
        box.checked = !box.checked;
        box.handlers.change();
    }
    states.push(state());
}
process.stdout.write(JSON.stringify(states));
