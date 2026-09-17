/** @odoo-module **/
import { Component, onWillStart, onWillUnmount, useEffect, useRef, useState } from '@odoo/owl';
import { loadBundle } from '@web/core/assets';

const short = new Intl.NumberFormat('es-MX', { notation: 'compact', maximumFractionDigits: 1 });
const BLUE = '#1946BA';
const GREEN = '#36751B';

export class AnalyticsChart extends Component {
    static template = 'hexagonos_analytics.Chart';
    static props = { spec: Object, openAction: Function };
    setup() {
        this.canvas = useRef('canvas');
        this.state = useState({ error: '' });
        onWillStart(async () => {
            try {
                await loadBundle('web.chartjs_lib');
                if (!window.Chart || Number(window.Chart.version.split('.')[0]) < 3) {
                    throw new Error('Se requiere Chart.js 3 o 4, incluido en Odoo 18.');
                }
            } catch (error) { this.state.error = 'Gráfica no disponible. Los valores y detalles siguen en la tabla.'; }
        });
        useEffect(() => { this.renderChart(); return () => this.chart?.destroy(); }, () => [this.props.spec]);
        onWillUnmount(() => this.chart?.destroy());
    }
    number(v) { return v === null || v === undefined ? 'Sin base' : new Intl.NumberFormat('es-MX', { maximumFractionDigits: this.props.spec.digits ?? 6 }).format(v); }
    renderChart() {
        if (this.state.error || !this.canvas.el) return;
        this.chart?.destroy();
        const spec = this.props.spec;
        const datasets = spec.datasets.map(ds => ({ borderWidth: 2, pointRadius: 4, tension: 0.15,
            ...ds, data: ds.data.map(v => typeof v === 'object' ? { ...v } : v) }));
        const scales = spec.heat ? {
            x: { type: 'linear', min: -0.5, max: spec.labels.length - 0.5,
                ticks: { stepSize: 1, color: '#42526B', callback: v => spec.labels[v] || '' }, grid: { display: false } },
            y: { type: 'linear', min: -0.5, max: spec.names.length - 0.5, reverse: true,
                ticks: { stepSize: 1, color: '#42526B', callback: v => spec.names[v] || '' }, grid: { display: false } },
        } : {
            x: { stacked: !!spec.stacked, ticks: { color: '#42526B', maxRotation: 40, autoSkip: true, font: { size: 12 } }, grid: { display: false } },
            y: { beginAtZero: true, stacked: !!spec.stacked, title: { display: true, text: spec.unit, color: '#42526B' },
                ticks: { color: '#42526B', callback: v => short.format(v) }, grid: { color: '#E4E9F1' } },
        };
        if (spec.pareto) scales.percent = { min: 0, max: 100, position: 'right',
            ticks: { color: '#42526B', callback: v => `${v}%` }, grid: { display: false } };
        try {
            this.chart = new window.Chart(this.canvas.el, {
                type: spec.type, data: { labels: [...spec.labels], datasets },
                options: {
                    responsive: true, maintainAspectRatio: false, animation: false,
                    interaction: { mode: spec.heat ? 'nearest' : 'index', intersect: spec.heat || false },
                    plugins: {
                        legend: { display: datasets.length > 1, labels: { color: '#24344D', boxWidth: 12, font: { size: 12 } } },
                        tooltip: { backgroundColor: '#0F2385', titleColor: '#FFFFFF', bodyColor: '#FFFFFF', padding: 12,
                            callbacks: {
                                title: items => {
                                    const c = items[0];
                                    return spec.heat ? `${spec.names[c.raw.y]} · ${spec.labels[c.raw.x]}` :
                                        (spec.datasets[c.datasetIndex].periods?.[c.dataIndex] || c.label);
                                },
                                label: c => `${c.dataset.label}: ${this.number(spec.heat ? c.raw.value : c.parsed.y)} ${c.dataset.yAxisID === 'percent' ? '%' : spec.unit}`,
                            } },
                    }, scales,
                    onClick: (event, elements) => {
                        if (!elements.length) return;
                        const item = elements[0];
                        const action = spec.datasets[item.datasetIndex].actions?.[item.index];
                        if (action) this.props.openAction(action);
                    },
                },
            });
        } catch (error) { this.state.error = 'No se pudo dibujar la gráfica. Consulta la tabla de valores.'; }
    }
}

export function commercialCharts(data, tab) {
    if (!data.commercial_available) return [];
    const panels = [];
    const dimensions = data.dimensions;
    const current = data.series[0]?.rows || [];
    const previous = data.series[1]?.rows || [];
    const n = Math.max(current.length, previous.length);
    if (tab === 'resumen' || tab === 'comercial') {
        panels.push({ key: 'trend', title: '¿Cómo evoluciona la venta?', type: 'line', unit: data.currency,
            note: 'Cada punto conserva las fechas exactas de su período. El último mes puede estar abierto.',
            labels: Array.from({ length: n }, (_, i) => `${current[i]?.label || '—'} / ${previous[i]?.label || '—'}`),
            datasets: [
                { label: 'Actual', data: Array.from({ length: n }, (_, i) => current[i]?.value ?? null), borderColor: BLUE,
                    backgroundColor: '#1946BA18', fill: true, periods: current.map(r => r.period), actions: current.map(r => r.action) },
                { label: 'Comparación', data: Array.from({ length: n }, (_, i) => previous[i]?.value ?? null), borderColor: GREEN,
                    borderDash: [6, 4], periods: previous.map(r => r.period), actions: previous.map(r => r.action) },
            ], rows: current.map((r, i) => ({ label: `${r.period} / ${previous[i]?.period || '—'}`, current: r.value,
                previous: previous[i]?.value ?? null, action: r.action, previous_action: previous[i]?.action })),
        });
        panels.push({ key: 'plants', title: 'Participación y evolución por almacén comercial', type: 'bar', stacked: true,
            unit: data.currency, note: 'Incluye Sin asignar. Cada línea de venta pertenece a un solo almacén comercial.',
            labels: current.map(r => r.label), datasets: dimensions.warehouse.map(wh => ({
                label: wh.label, backgroundColor: wh.color, data: current.map(r => r.warehouses[String(wh.id)] || 0),
                actions: current.map(r => ({ ...r.action, domain: [...r.action.domain, ['hmx_warehouse_id', '=', wh.id || false]] })),
            })), rows: dimensions.warehouse,
        });
        for (const [dimension, label] of [['warehouse', 'almacén'], ['product', 'producto'], ['customer', 'cliente']]) {
            const rows = dimensions[`${dimension}_drivers`];
            panels.push({ key: `drivers_${dimension}`, title: `¿Qué ${label} explica la variación?`, type: 'bar', unit: data.currency,
                note: 'Aportación aritmética = actual − anterior. Las barras, incluido Otros, suman la variación total; no prueban causalidad.',
                labels: rows.map(r => r.label), datasets: [{ label: 'Diferencia absoluta', data: rows.map(r => r.difference),
                    backgroundColor: rows.map(r => r.difference < 0 ? '#AD3037' : BLUE), actions: rows.map(r => r.action) }], rows,
            });
        }
        if (tab === 'comercial' && data.invoice_warehouses) {
            const rows = data.invoice_warehouses;
            panels.push({ key: 'invoice_warehouses', title: 'Facturación neta por almacén comercial', type: 'bar', unit: data.currency,
                note: 'Líneas contables publicadas, netas de notas de crédito. Sin enlace único a ventas: Sin asignar. No son entregas ni cobros.',
                labels: rows.map(r => r.label), datasets: [{ label: 'Actual', backgroundColor: BLUE,
                    data: rows.map(r => r.current), actions: rows.map(r => r.action) },
                    { label: 'Anterior', backgroundColor: GREEN, data: rows.map(r => r.previous), actions: rows.map(r => r.previous_action) }], rows });
        }
    }
    const ranks = tab === 'clientes' ? [['customer', 'clientes']] : tab === 'productos' ? [['product', 'productos'], ['family', 'familias']] :
        tab === 'resumen' ? [['customer', 'clientes'], ['product', 'productos']] : [];
    for (const [dim, name] of ranks) {
        const rows = dimensions[dim];
        const pareto = rows.every(r => r.cumulative !== null);
        panels.push({ key: `pareto_${dim}`, title: `Concentración de ${name}`, type: 'bar', pareto, unit: data.currency,
            note: pareto ? 'Importe y porcentaje acumulado. Otros conserva el total de la selección.' : 'El acumulado no aplica con importes netos negativos o total cero.',
            labels: rows.map(r => r.label), datasets: [
                { label: 'Ventas', backgroundColor: BLUE, data: rows.map(r => r.current), actions: rows.map(r => r.action) },
                ...(pareto ? [{ type: 'line', label: '% acumulado', yAxisID: 'percent', borderColor: GREEN,
                    data: rows.map(r => r.cumulative), actions: rows.map(r => r.action) }] : []),
            ], rows,
        });
    }
    return panels.map(panel => ({ ...panel, digits: data.currency_digits ?? 2 }));
}

export function productionCharts(data) {
    const production = data.production;
    if (!production?.available) return [];
    const units = [...new Set(production.chart_series.map(r => r.unit_id))];
    return units.map(unitId => {
        const rows = production.chart_series.filter(r => r.unit_id === unitId);
        const unit = rows[0].unit;
        return { key: `production_${unitId}`, title: `Producción registrada · ${unit}`, type: 'line', unit,
            labels: data.months, note: 'Unidades exactas, separadas por almacén de fabricación. Sin movimientos no demuestra un paro físico.',
            datasets: rows.map(row => ({ label: row.label, borderColor: row.color,
                data: row.points.map(p => p.value), actions: row.points.map(p => p.action),
            })), rows,
        };
    });
}
