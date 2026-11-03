// Execute wg-switch.js with the meeting menu, its hint and a localStorage.
// No network or third-party browser packages are needed.
const fs = require('node:fs');
const vm = require('node:vm');
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const stored = new Map(Object.entries(input.stored ?? {}));
const head = [];
const docListeners = {};
let focused = false;
function element(tag, extra = {}) {
    return Object.assign({
        tagName: tag, handlers: {}, classes: [],
        classList: {contains(name) { return this.owner.classes.includes(name); }},
        addEventListener(k, fn) { (this.handlers[k] ??= []).push(fn); },
        fire(k, event = {}) { (this.handlers[k] ?? []).forEach(fn => fn(event)); },
    }, extra);
}
function own(el) { el.classList.owner = el; return el; }
const summary = own(element('summary', {focus() { focused = true; }}));
const items = input.items.map(item => own(element('a', {
    href: 'https://example.test' + item.href, classes: item.past ? ['wg-switch-past'] : []})));
const menu = own(element('details', {
    open: false,
    contains: target => target === menu || target === summary || items.includes(target),
    querySelector: selector => selector === 'summary' ? summary : null,
    querySelectorAll: selector => selector === '.wg-switch-item:not([aria-current])' ? items : [],
}));
const button = own(element('button'));
const hint = own(element('p', {hidden: true, querySelector: () => button}));
const guard = fn => (...args) => { if (input.storageError) throw Error('disabled'); return fn(...args); };
const context = {
    JSON, Array,
    HTMLScriptElement: input.rules === undefined ? {} : {supports: type => input.rules && type === 'speculationrules'},
    document: {
        getElementById: id => ({'wg-switch': menu, 'wg-switch-hint': hint})[id] ?? null,
        createElement: tag => element(tag),
        addEventListener(k, fn) { (docListeners[k] ??= []).push(fn); },
        head: {appendChild: el => head.push(el)},
    },
    localStorage: {
        getItem: guard(k => stored.get(k) ?? null),
        setItem: guard((k, v) => { stored.set(k, v); }),
    },
};
vm.runInNewContext(input.script, context);
function setOpen(open) { menu.open = open; menu.fire('toggle'); }
function state() {
    return {
        hint: hint.hidden ? 'hidden' : 'shown',
        open: menu.open,
        focused,
        stored: Object.fromEntries(stored),
        head: head.map(el => el.tagName === 'script' ? {type: el.type, rules: JSON.parse(el.textContent)}
                                                     : {rel: el.rel, href: el.href}),
    };
}
const states = [state()];
for (const step of input.steps ?? []) {
    if (step === 'pointerenter') summary.fire('pointerenter');
    else if (step === 'open') setOpen(true);
    else if (step === 'close') setOpen(false);
    else if (step === 'got-it') button.fire('click');
    else if (step === 'click-outside') (docListeners.click ?? []).forEach(fn => fn({target: {}}));
    else if (step === 'click-item') (docListeners.click ?? []).forEach(fn => fn({target: items[0]}));
    else if (step === 'escape') (docListeners.keydown ?? []).forEach(fn => fn({key: 'Escape'}));
    else if (step.storeElsewhere) {
        // Another page (the one the visitor came from) writes the storage.
        stored.set(step.storeElsewhere[0], step.storeElsewhere[1]);
    } else if (step === 'activate') (docListeners.prerenderingchange ?? []).forEach(fn => fn({}));
    states.push(state());
}
process.stdout.write(JSON.stringify(states));
