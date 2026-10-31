// Execute wg-experimental.js with the status-line button and its note.
const fs = require('node:fs');
const vm = require('node:vm');
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const docListeners = {};
let focused = false;
const toggle = {
    handlers: {}, attrs: {},
    addEventListener(k, fn) { (this.handlers[k] ??= []).push(fn); },
    setAttribute(k, v) { this.attrs[k] = v; },
    focus() { focused = true; },
};
const note = {hidden: true, contains: target => target === note};
vm.runInNewContext(input.script, {
    String,
    document: {
        getElementById: id => ({'wg-experimental': toggle, 'wg-experimental-note': note})[id] ?? null,
        addEventListener(k, fn) { (docListeners[k] ??= []).push(fn); },
    },
});
// As a browser delivers a click: capture on the document first, then the target.
function click(target) {
    (docListeners.click ?? []).forEach(fn => fn({target}));
    if (target === toggle) toggle.handlers.click.forEach(fn => fn({target}));
}
function state() { return {note: note.hidden ? 'hidden' : 'shown', expanded: toggle.attrs['aria-expanded'] ?? null, focused}; }
const states = [state()];
for (const step of input.steps ?? []) {
    if (step === 'click-toggle') click(toggle);
    else if (step === 'click-note') click(note);
    else if (step === 'click-outside') click({});
    else if (step === 'escape') (docListeners.keydown ?? []).forEach(fn => fn({key: 'Escape'}));
    states.push(state());
}
process.stdout.write(JSON.stringify(states));
