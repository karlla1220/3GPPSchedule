// Execute the header's help script on a page without a grid: walk the tour
// with Next, Back and the ways out, and report what each step showed.
const fs = require('node:fs');
const vm = require('node:vm');
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const touched = [];
const scrolls = [];
let scrollY = input.scrollY ?? 0;

function classes() {
    const set = new Set();
    return {add: (...c) => c.forEach(x => set.add(x)), remove: (...c) => c.forEach(x => set.delete(x)),
            toggle: (c, on) => (on ?? !set.has(c)) ? set.add(c) : set.delete(c), contains: c => set.has(c)};
}
function element(rect = {top: 0, left: 0, width: 10, height: 10}) {
    const r = {...rect, right: rect.left + rect.width, bottom: rect.top + rect.height};
    return {
        hidden: false, style: {}, classList: classes(), textContent: '', innerHTML: '', offsetHeight: 0, disabled: false,
        getBoundingClientRect: () => r, getClientRects: () => [r],
        addEventListener(event, fn) { this['on' + event] = fn; },
        focus() { this.focused = true; }, click() { if (this.onclick) this.onclick(); }, parentElement: null,
    };
}
const button = element();
const card = Object.assign(element({top: 600, left: 100, width: 400, height: 150}), {offsetHeight: 150});
const parts = {'.help-spot': element(), '.help-card': card, '.help-count': element(), '#help-title': element(),
               '#help-text': element(), '.help-stage': element(), '.help-back': element(), '.help-next': element(),
               '.help-close': element()};
const guide = Object.assign(element(), {
    open: false,
    querySelector: sel => parts[sel],
    showModal() { this.open = true; },
    close() { this.open = false; this.onclose(); },
});
if (!input.dialog) delete guide.showModal;
const targets = {};
for (const [sel, state] of Object.entries(input.targets)) {
    targets[sel] = Object.assign(element({top: 2000, left: 20, width: 300, height: 100}), {hidden: state === 'hidden'});
}
if (input.grid) {
    // A grid whose sessions are all out of view: no "cell" step, but a "grid" one.
    targets['.day-panel.active .schedule-grid'] = Object.assign(element(), {
        querySelector: () => ({}), querySelectorAll: () => [], parentElement: element({top: 900, left: 0, width: 1000, height: 1500}),
    });
}
const storage = new Proxy({}, {get(_, key) { touched.push(String(key)); return () => null; }});
const window = {
    innerWidth: 1000, innerHeight: 800,
    get scrollX() { return 0; }, get scrollY() { return scrollY; },
    scrollTo(options) { scrolls.push(options); scrollY = options.top; },
    matchMedia: query => ({matches: query.includes('coarse') ? !!input.touch : false}),
    addEventListener() {}, removeEventListener() {},
};
vm.runInNewContext(input.script, {
    window, setTimeout, requestAnimationFrame: fn => fn(), localStorage: storage, sessionStorage: storage,
    getComputedStyle: () => ({position: 'static'}),
    document: {
        body: {}, documentElement: {scrollHeight: 5000},
        getElementById: id => ({'help-button': button, 'help-guide': guide})[id] || null,
        querySelector: sel => targets[sel] || null,
        querySelectorAll: () => [],
    },
});
const states = [];
for (const step of input.steps) {
    if (step === 'start') button.onclick();
    if (step === 'next') parts['.help-next'].onclick();
    if (step === 'back') parts['.help-back'].onclick();
    if (step === 'close') parts['.help-close'].onclick();
    if (step === 'escape') guide.close();
    if (step === 'outside') guide.onclick({target: guide, clientX: 900, clientY: 10});
    if (step === 'right') guide.onkeydown({key: 'ArrowRight'});
    states.push({
        open: guide.open, buttonHidden: button.hidden, focused: !!button.focused, scrollY,
        count: parts['.help-count'].textContent, title: parts['#help-title'].textContent,
        next: parts['.help-next'].textContent, backDisabled: parts['.help-back'].disabled,
    });
}
process.stdout.write(JSON.stringify({states, touched, scrolls}));
