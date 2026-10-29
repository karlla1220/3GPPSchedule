// Execute switch-arrival.js for one arrival: where the page is, where the
// visitor came from, and how. Prints what the script wrote into <head>.
const fs = require('node:fs');
const vm = require('node:vm');
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const written = [];
const here = new URL(input.location);
vm.runInNewContext(input.script, {
    URL,
    location: {origin: here.origin, pathname: here.pathname},
    document: {referrer: input.referrer ?? '', write: html => written.push(html)},
    performance: {getEntriesByType: () => input.navType ? [{type: input.navType}] : []},
});
process.stdout.write(JSON.stringify(written));
