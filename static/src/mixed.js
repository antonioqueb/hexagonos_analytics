/** @odoo-module **/

// Presentation only: amounts and actions come unchanged from each server currency block.
// Never add money across currencies or infer missing ranked/history values as zero.
const currencyMeta = block => ({ currency_id: block.currency_id, currency: block.currency,
    currency_digits: block.currency_digits });

export function mixedViewData(data) {
    if (!data.mixed) return data;
    const blocks = data.currency_blocks;
    const cards = new Map(), customers = new Map(), cohorts = new Map(), insights = new Map();
    for (const block of blocks) {
        const currency = currencyMeta(block);
        for (const card of block.cards) {
            if (!cards.has(card.key)) cards.set(card.key, { key: card.key, label: card.label, definition: card.definition, currency_values: [] });
            cards.get(card.key).currency_values.push({ ...card, ...currency });
        }
        for (const row of block.customers?.rows || []) {
            if (!customers.has(row.id)) {
                const { current, previous, difference, percent, share, ...identity } = row;
                customers.set(row.id, { ...identity, currency_values: [] });
            }
            const target = customers.get(row.id);
            // History and status are company-wide on the server, independent of currency.
            target.currency_values.push({ ...row, ...currency });
            if (!target.chart_code && row.chart_code) {
                target.chart_code = row.chart_code;
                target.chart_name = row.chart_name;
            }
        }
        for (const cohort of block.customers?.cohorts || []) {
            if (!cohorts.has(cohort.key)) cohorts.set(cohort.key, { key: cohort.key, label: cohort.label, currency_values: [] });
            cohorts.get(cohort.key).currency_values.push({ ...cohort, ...currency });
        }
        // Select contributors within each currency, never by comparing MXN amounts to USD.
        for (const row of [...(block.dimensions.warehouse || [])].sort((a, b) => Math.abs(b.difference) - Math.abs(a.difference)).slice(0, 3)) {
            if (!insights.has(row.id)) insights.set(row.id, { id: row.id, label: row.label });
        }
    }
    const customerRows = [...customers.values()];
    const months = blocks.find(block => block.customers)?.customers.months || [];
    for (const row of customerRows) {
        row.months = row.currency_values.some(value => value.months.length) ? months.map(label => ({ label, currency_values: row.currency_values.map(value => {
            const month = value.months.find(item => item.label === label);
            return { ...currencyMeta(value), ...(month || { label, value: null, action: null }) };
        }) })) : [];
    }
    return { ...blocks[0], ...data, currency: blocks.map(block => block.currency).join(' · '),
        commercial_available: blocks.some(block => block.commercial_available),
        restricted_currencies: blocks.filter(block => !block.commercial_available).map(block => block.currency),
        cards: [...cards.values()],
        partial: blocks.find(block => block.partial)?.partial || null,
        partial_values: blocks.filter(block => block.partial).map(block => ({ ...block.partial, ...currencyMeta(block) })),
        insights: [...insights.values()].map(insight => ({ ...insight, currency_values: blocks.flatMap(block => {
            const row = block.dimensions.warehouse?.find(item => item.id === insight.id);
            return row ? [{ ...row, ...currencyMeta(block) }] : [];
        }) })),
        reconciliations: blocks.filter(block => block.reconciliation).map(block => ({ ...block.reconciliation, ...currencyMeta(block) })),
        customers: blocks.some(block => block.customers) ? { months, rows: customerRows,
            cohorts: [...cohorts.values()].map(cohort => ({ ...cohort,
                count: customerRows.filter(row => row.status === cohort.key).length })) } : null,
        volume: blocks.flatMap(block => block.volume.map(row => ({ ...row, ...currencyMeta(block), id: `${block.currency_id}/${row.id}` }))),
        family_volume: blocks.flatMap(block => (block.family_volume || []).map(row => ({ ...row, ...currencyMeta(block), id: `${block.currency_id}/${row.id}` }))),
        notes: ['Mixto presenta todas las divisas en una sola vista. Los importes y las escalas conservan su moneda original, sin conversión ni suma entre divisas.',
            ...new Set(blocks.flatMap(block => block.notes.filter(note => !note.includes('moneda original, sin conversión ni suma entre monedas'))))],
    };
}

export function mixedHeatRows(data) {
    // Alternate each currency's ranking: no global ranking based on incompatible amounts.
    const rankings = data.currency_blocks.map(block => [...(block.customers?.rows || [])]
        .filter(row => row.months.length).sort((a, b) => b.current - a.current));
    const ids = new Set();
    for (let rank = 0; rank < 15 && ids.size < 15; rank++) {
        for (const rows of rankings) {
            if (rows[rank]) ids.add(rows[rank].id);
            if (ids.size === 15) break;
        }
    }
    const indexed = new Map(data.customers.rows.map(row => [row.id, row]));
    return [...ids].map(id => indexed.get(id));
}
