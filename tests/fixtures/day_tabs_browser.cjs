// Execute day-select.js where it runs in the page (after the day tabs, before
// the panels are drawn), then schedule.js at DOMContentLoaded, with the DOM
// both use. No network or third-party browser packages are needed.
const fs = require('node:fs');
const vm = require('node:vm');
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const now = Date.parse(input.now);
const RealDate = Date;
class ClockDate extends RealDate {
    constructor(...args) { super(...(args.length ? args : [now])); }
    static now() { return now; }
}
const session = new Map(Object.entries(input.session ?? {}));
const byId = new Map();
const listeners = {};
const scrolls = [];
function element(tag) {
    const classes = new Set();
    const el = {
        tagName: tag, dataset: {}, attrs: {}, handlers: {}, style: {setProperty() {}},
        classList: {
            add: name => classes.add(name),
            remove: name => classes.delete(name),
            contains: name => classes.has(name),
            toggle: (name, on) => ((on ?? !classes.has(name)) ? classes.add(name) : classes.delete(name), classes.has(name)),
        },
        setAttribute(k, v) { this.attrs[k] = v; },
        addEventListener(k, fn) { (this.handlers[k] ??= []).push(fn); },
        click() { (this.handlers.click ?? []).forEach(fn => fn.call(this, {target: this})); },
        remove() { if (byId.get(this.id) === this) byId.delete(this.id); },
        querySelector: () => null,
        querySelectorAll: () => [],
        appendChild() {},
    };
    return el;
}
const tabs = input.days.map(day => Object.assign(element('button'), {dataset: {day}}));
const panels = input.days.map(day => {
    const panel = Object.assign(element('div'), {id: day});
    byId.set(day, panel);
    return panel;
});
for (const id of ['popup-backdrop', 'popup-close-btn', 'popup-floating', 'popup-content', 'now-toggle']) {
    byId.set(id, Object.assign(element('div'), {id}));
}
// The tab bar is as wide as the grid's box; day-select.js sizes the zoomed grid by it.
const toolbar = Object.assign(element('div'), {clientWidth: input.toolbarWidth ?? 396});
const rootVars = {};
function matchTab(selector) {
    const day = /\[data-day="([^"]*)"\]/.exec(selector);
    return tabs.filter(tab => (!day || tab.dataset.day === day[1])
        && (!selector.includes('.active') || tab.classList.contains('active')));
}
const document = {
    getElementById: id => byId.get(id) ?? null,
    querySelector: selector => selector === '.schedule-toolbar' ? toolbar
        : (/^(\.tab|\[data-day)/.test(selector) ? matchTab(selector)[0] : null) ?? null,
    querySelectorAll: selector => selector === '.tab' ? tabs : selector === '.day-panel' ? panels : [],
    createElement: element,
    addEventListener(event, fn) { listeners[event] = fn; },
    head: {appendChild(el) { byId.set(el.id, el); }},
    documentElement: {clientWidth: 1440, clientHeight: 900, style: {
        setProperty(k, v) { rootVars[k] = v; }, removeProperty(k) { delete rootVars[k]; }}},
};
function storage(map) {
    const guard = fn => (...args) => { if (input.storageError) throw Error('disabled'); return fn(...args); };
    return {getItem: guard(k => map.get(k) ?? null), setItem: guard((k, v) => { map.set(k, String(v)); })};
}
const context = {
    Date: ClockDate, Intl, Number, Math, JSON, Set, String, isFinite, encodeURIComponent, decodeURIComponent,
    CSS: {escape: value => value.replace(/[^\w-]/g, ch => '\\' + ch),
          supports: prop => input.zoomSupported !== false && prop === 'zoom'},
    getComputedStyle: () => ({getPropertyValue: k => k === '--grid-min-width' ? '600px' : ''}),
    document,
    location: {pathname: '/ran1/', search: '', hash: ''},
    history: {replaceState() {}},
    window: {addEventListener() {}, scrollTo: (x, y) => scrolls.push([x, y]), scrollX: 0, scrollY: 0},
    sessionStorage: storage(session),
    localStorage: storage(new Map()),
    setInterval() {}, setTimeout: () => 0, clearTimeout() {},
};
function state() {
    const rule = byId.get('initial-day');
    return {
        activeTabs: tabs.filter(tab => tab.classList.contains('active')).map(tab => tab.dataset.day),
        activePanels: panels.filter(panel => panel.classList.contains('active')).map(panel => panel.id),
        rule: rule ? rule.textContent : null,
        scrolls: [...scrolls],
        vars: {...rootVars},
    };
}
vm.runInNewContext(input.daySelect, context);
const states = [state()];
if (input.page) {
    context.window.scrollTo = (x, y) => scrolls.push([x, y]);
    vm.runInNewContext(input.page, context);
    listeners.DOMContentLoaded();
    states.push(state());
    for (const day of input.clicks ?? []) {
        tabs.find(tab => tab.dataset.day === day).click();
        states.push(state());
    }
}
process.stdout.write(JSON.stringify(states));
