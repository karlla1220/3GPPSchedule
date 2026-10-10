// Execute the header's support-note script with a clock and stored dismissal.
const fs = require('node:fs');
const vm = require('node:vm');
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const stored = {...(input.stored ?? {})};
const close = {addEventListener(event, fn) { this.click = fn; }};
const note = {hidden: true, querySelector: () => close};
vm.runInNewContext(input.script, {
    Date: {now: () => Date.parse(input.now)},
    document: {getElementById: id => id === 'support-note' ? note : null},
    localStorage: {
        getItem(key) { if (input.storageError) throw Error('disabled'); return stored[key] ?? null; },
        setItem(key, value) { if (input.storageError) throw Error('disabled'); stored[key] = value; },
    },
});
const shown = !note.hidden;
if (input.dismiss) close.click();
process.stdout.write(JSON.stringify({shown, hiddenAfter: note.hidden, stored}));
