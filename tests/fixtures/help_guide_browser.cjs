// Execute the header's help script against a page without a grid: open and
// close the guide, and report what it showed and touched.
const fs = require('node:fs');
const vm = require('node:vm');
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const touched = [];

function target() {
    return {addEventListener(event, fn) { this['on' + event] = fn; }};
}
const button = Object.assign(target(), {hidden: false, focus() { this.focused = true; }});
const body = {scrollTop: 40};
const closeButton = target();
const sections = input.scenes.map(scene => {
    const stage = {innerHTML: 'stale'};
    return {dataset: {scene}, hidden: false, stage, classList: {add() {}}, querySelector: () => stage};
});
const guide = Object.assign(target(), {
    open: false,
    querySelector: sel => ({'.help-guide-body': body, '.help-guide-close': closeButton})[sel],
    querySelectorAll: sel => sel === '.help-scene' ? sections : sections.map(s => s.stage),
    close() { this.open = false; this.onclose(); },
});
if (input.dialog) guide.showModal = function() { this.open = true; };
const elements = {'help-button': button, 'help-guide': guide};
if (input.agreements) elements['agreement-panel'] = {};
const storage = new Proxy({}, {get(_, key) { touched.push(String(key)); return () => null; }});

class IntersectionObserver { observe() {} disconnect() {} }
const window = {matchMedia: () => ({matches: false}), IntersectionObserver};
vm.runInNewContext(input.script, {
    window, IntersectionObserver, setTimeout, localStorage: storage, sessionStorage: storage,
    document: {
        getElementById: id => elements[id] || null,
        querySelector: () => null,
        querySelectorAll: () => [],
    },
});
const states = [];
const state = () => ({
    buttonHidden: button.hidden, open: guide.open, scrollTop: body.scrollTop, focused: !!button.focused,
    hidden: Object.fromEntries(sections.map(s => [s.dataset.scene, s.hidden])),
    stages: sections.map(s => s.stage.innerHTML),
});
for (const step of input.steps) {
    if (step === 'open') button.onclick();
    if (step === 'close') closeButton.onclick();
    if (step === 'backdrop') guide.onclick({target: guide});
    if (step === 'inside') guide.onclick({target: body});
    states.push(state());
}
process.stdout.write(JSON.stringify({states, touched}));
