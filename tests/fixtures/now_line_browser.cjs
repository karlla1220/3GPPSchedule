// Execute the generated page script with a clock and the DOM used by NOW.
// No network or third-party browser packages are needed.
const fs = require('node:fs');
const vm = require('node:vm');
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
let now = Date.parse(input.now);
const RealDate = Date;
class ClockDate extends RealDate {
    constructor(...args) { super(...(args.length ? args : [now])); }
    static now() { return now; }
}
const elements = new Map();
let lines = [];
const listeners = {};
const windowListeners = {};
const intervals = [];
const timers = new Map();
let nextTimer = 0;
let stored = input.stored ?? null;
function element() {
    return {
        dataset: {}, style: {}, attrs: {}, handlers: {},
        setAttribute(k, v) { this.attrs[k] = v; },
        addEventListener(k, fn) { this.handlers[k] = fn; },
        remove() { lines = lines.filter(line => line !== this); },
    };
}
const button = element();
elements.set('now-toggle', button);
elements.set('popup-backdrop', element());
elements.set('popup-close-btn', element());
for (const day of ['monday', 'tuesday', 'wednesday', 'thursday', 'friday']) {
    const grid = element();
    grid.dataset = {start: '480', end: '1080', slot: '5'};
    grid.appendChild = line => lines.push(line);
    const panel = element();
    panel.querySelector = () => grid;
    elements.set(day, panel);
}
const context = {
    Date: ClockDate, Intl, Number, Math, JSON,
    document: {
        getElementById: id => elements.get(id) ?? null,
        querySelector: () => null,
        querySelectorAll: selector => selector === '.now-line' ? [...lines] : [],
        createElement: element,
        addEventListener(event, fn) { listeners[event] = fn; },
    },
    window: {addEventListener(event, fn) { windowListeners[event] = fn; }},
    localStorage: {
        getItem() { if (input.storageError) throw Error('disabled'); return stored; },
        setItem(k, value) { if (input.storageError) throw Error('disabled'); stored = value; },
    },
    sessionStorage: {getItem: () => null},
    setInterval(fn) { intervals.push(fn); },
    setTimeout(fn, delay) { timers.set(++nextTimer, {fn, at: now + delay}); return nextTimer; },
    clearTimeout(id) { timers.delete(id); },
};
vm.runInNewContext(input.script, context);
listeners.DOMContentLoaded();
function state() {
    return {pressed: button.attrs['aria-pressed'], disabled: button.disabled,
            lines: lines.length, stored, nextTimer: Math.min(...[...timers.values()].map(t => t.at))};
}
const states = [state()];
for (const step of input.steps ?? []) {
    if (step.now) now = Date.parse(step.now);
    if (step.click) button.handlers.click();
    else if (step.focus) windowListeners.focus();
    else if (step.visible) listeners.visibilitychange();
    else if (step.timerOnly) {
        for (const [id, timer] of [...timers]) {
            if (timer.at <= now) { timers.delete(id); timer.fn(); }
        }
    } else intervals.forEach(fn => fn());
    states.push(state());
}
process.stdout.write(JSON.stringify(states));
