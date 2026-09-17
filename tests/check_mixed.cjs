// Presentation regressions using the actual server-generated, synthetic fixtures.
const fs = require('fs'), path = require('path'), vm = require('vm'), assert = require('node:assert/strict');
const source = fs.readFileSync(path.join(__dirname, '../static/src/mixed.js'), 'utf8').replace(/export function /g, 'function ');
const context = vm.createContext({});
vm.runInContext(source, context);
const fixtures = JSON.parse(fs.readFileSync(path.join(process.argv[2], 'fixtures.json'), 'utf8'));
const snapshot = JSON.stringify(fixtures);
const sourceData = fixtures.mixed.resumen;
const result = context.mixedViewData(sourceData);
assert.equal(result.cards.length, 3);
assert.ok(result.cards.every(card => !('current' in card))); // No invented cross-currency total.
for (const block of sourceData.currency_blocks) {
    for (const expected of block.cards) {
        const actual = result.cards.find(card => card.key === expected.key).currency_values.find(row => row.currency_id === block.currency_id);
        for (const field of ['current', 'previous', 'difference', 'percent', 'available', 'record_count', 'action', 'previous_action']) {
            assert.deepEqual(actual[field], expected[field]);
        }
    }
}
const ids = new Set(sourceData.currency_blocks.flatMap(block => block.customers.rows.map(row => row.id)));
assert.equal(result.customers.rows.length, ids.size);
assert.equal(result.customers.cohorts.reduce((total, row) => total + row.count, 0), ids.size);
assert.equal(result.customers.cohorts.find(row => row.key === 'recurring').count, 2);
assert.strictEqual(result.production, sourceData.production);
assert.equal(JSON.stringify(fixtures), snapshot);
console.log('PASS amounts/comparisons/domains preserved per currency; unique customers; production once; no input mutation');

// A missing monthly breakdown is not evidence of zero purchases.
const missing = structuredClone(sourceData);
missing.currency_blocks[1].customers.rows[0].months = [];
const missingView = context.mixedViewData(missing);
const shared = missingView.customers.rows.find(row => row.id === 1);
assert.ok(shared.months.every(month => month.currency_values.find(row => row.currency === 'USD').value === null));
assert.ok(shared.months.every(month => month.currency_values.find(row => row.currency === 'USD').action === null));
const heat = context.mixedHeatRows(missingView);
assert.equal(new Set(heat.map(row => row.id)).size, heat.length);
assert.ok(heat.length <= 15);
console.log('PASS heatmap distinguishes omitted detail from zero and deduplicates customers');

// More currencies, different precision, denied figures and no-history currencies.
const expanded = structuredClone(sourceData);
const extra = structuredClone(expanded.currency_blocks[1]);
extra.currency_id = 3; extra.currency = 'JPY'; extra.currency_digits = 0;
extra.customers = null; extra.volume = []; extra.family_volume = [];
extra.cards[0] = { ...extra.cards[0], current: 0, previous: 0, difference: 0, percent: null };
extra.cards[1] = { key: 'invoiced', label: 'Facturación neta', available: false, reason: 'Sin acceso', definition: 'Contabilizado' };
expanded.currency_blocks.push(extra);
const expandedView = context.mixedViewData(expanded);
assert.equal(expandedView.cards.length, 3);
const zero = expandedView.cards[0].currency_values.find(row => row.currency === 'JPY');
assert.equal(zero.current, 0); assert.equal(zero.percent, null); assert.equal(zero.currency_digits, 0);
const denied = expandedView.cards[1].currency_values.find(row => row.currency === 'JPY');
assert.equal(denied.available, false); assert.ok(!('current' in denied));
assert.equal(expandedView.customers.rows.length, ids.size);
assert.strictEqual(context.mixedViewData(fixtures.dashboards.resumen), fixtures.dashboards.resumen);
console.log('PASS third currency, currency precision, zero base, restricted source and single-currency behavior');
