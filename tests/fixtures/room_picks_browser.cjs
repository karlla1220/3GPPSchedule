// Execute room-picks.js with the DOM it uses, where it runs in the page: after
// the checkboxes, before the grid. No browser packages are needed.
const fs = require('node:fs');
const vm = require('node:vm');
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const stored = new Map(input.saved === null ? [] : [['3gpp_schedule_rooms:ran1:M', input.saved]]);
const boxes = input.names.map((name, room) => ({checked: true, disabled: false, dataset: {room: String(room), name}}));
const handlers = {};
const box = {
    dataset: {week: JSON.stringify(input.week)},
    querySelectorAll: () => boxes,
    addEventListener(event, fn) { handlers[event] = fn; },
};
const tab = {dataset: {}};
const style = {textContent: ''};
const events = [];
const document = {
    querySelector: selector => selector === '.room-picks' ? box : tab,
    createElement: () => style,
    head: {appendChild() {}},
    dispatchEvent: event => events.push(event.type),
};
vm.runInNewContext(input.script, {
    document, JSON, Array, Set, Number, String,
    CustomEvent: class { constructor(type) { this.type = type; } },
    localStorage: {
        getItem: key => stored.get(key) ?? null,
        setItem: (key, value) => stored.set(key, value),
        removeItem: key => stored.delete(key),
    },
});
for (const name of input.uncheck) {
    boxes.find(b => b.dataset.name === name).checked = false;
    handlers.change();
}
process.stdout.write(JSON.stringify({
    rule: style.textContent, width: tab.dataset.gridMinWidth, events,
    checked: boxes.map(b => b.checked), disabled: boxes.map(b => b.disabled),
    stored: JSON.parse(stored.get('3gpp_schedule_rooms:ran1:M') ?? '[]'),
}));
