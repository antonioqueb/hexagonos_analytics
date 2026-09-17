/** @odoo-module **/

import { registry } from '@web/core/registry';
import { useService } from '@web/core/utils/hooks';
import { Dialog } from '@web/core/dialog/dialog';
import { Component, onWillStart, onWillUnmount, useState } from '@odoo/owl';
import { user } from '@web/core/user';
import { AnalyticsChart, commercialCharts, productionCharts } from './charts';

const MODEL = 'hexagonos.analytics';
const numberFormat = new Intl.NumberFormat('es-MX', { maximumFractionDigits: 6 });

// Calendar operations use UTC dates; business "today" comes from the server.
function shiftDate(value, days) {
    const date = new Date(`${value}T12:00:00Z`);
    date.setUTCDate(date.getUTCDate() + days);
    return date.toISOString().slice(0, 10);
}

export class AnalyticsDetail extends Component {
    static template = 'hexagonos_analytics.Detail';
    static components = { Dialog };
    static props = { close: Function, detail: Object, openAction: Function, openRecord: Function };
    number(value) { return value === null ? 'Sin base' : numberFormat.format(value); }
}

export class HexagonosAnalytics extends Component {
    static template = 'hexagonos_analytics.Dashboard';
    static props = ['*'];
    static components = { AnalyticsChart };

    setup() {
        this.orm = useService('orm');
        this.action = useService('action');
        this.dialog = useService('dialog');
        this.notification = useService('notification');
        this.requestId = 0;
        this.detailId = 0;
        this.alive = true;
        this.state = useState({
            tab: 'resumen', options: null, data: null, loading: true, authorized: true,
            error: '', preset: 'custom', detailLoading: false, customerPage: 0, customerStatus: '', customerSearch: '', volumePage: 0, volumeSearch: '',
            filters: { date_from: '', date_to: '', comparison: 'previous_period', warehouse_ids: [],
                customer_id: 0, product_id: 0, family_id: 0, seller_id: 0, currency_id: 0,
                inactivity_days: 90, cadence_factor: 2 },
        });
        this.filterCatalogs = [{ key: 'customer', label: 'Cliente · comercial' }, { key: 'product', label: 'Producto · venta y producción' },
            { key: 'family', label: 'Familia · venta y producción' }, { key: 'seller', label: 'Vendedor · comercial' }];
        this.catalogRequests = {};
        this.presets = [{ key: 'mes', label: 'Este mes' }, { key: '30', label: '30 días' },
            { key: '90', label: '90 días' }, { key: '365', label: '365 días' }];
        onWillStart(async () => {
            // Fail before mounting if permission was revoked or a direct URL is used.
            // Odoo handles the AccessError; no empty dashboard is rendered.
            const options = await this.orm.call(MODEL, 'get_options', []);
            if (!this.alive) return;
            this.state.options = options;
            this.storageKey = `hmx.analytics.v2.${user.userId}.${options.company_id}`;
            let saved;
            try { saved = JSON.parse(sessionStorage.getItem(this.storageKey) || 'null'); } catch { /* optional session storage */ }
            this.state.filters = { ...this.state.filters, date_from: options.date_from, date_to: options.today,
                currency_id: options.currency_id, ...(saved?.filters || {}) };
            if (saved?.tab && options.tabs.some(t => t.key === saved.tab)) this.state.tab = saved.tab;
            for (const kind of this.filterCatalogs.map(c => c.key)) {
                if (this.state.filters[`${kind}_id`]) await this.searchCatalog(kind, '');
            }
            await this.load();
        });
        onWillUnmount(() => { this.alive = false; this.requestId++; this.detailId++; });
    }

    errorMessage(error) {
        return error.data?.message || error.message || 'No fue posible consultar los indicadores.';
    }

    hideOnAccessError(error) {
        if (error.data?.name === 'odoo.exceptions.AccessError') {
            this.state.authorized = false;
            this.state.data = null;
            this.notification.add(this.errorMessage(error), { type: 'danger' });
        }
    }

    async load(tab = this.state.tab) {
        const request = ++this.requestId;
        this.detailId++;
        this.state.detailLoading = false;
        this.state.tab = tab;
        this.state.volumePage = 0;
        this.state.customerPage = 0;
        this.state.loading = true;
        this.state.error = '';
        this.state.data = null;
        const filters = { ...this.state.filters, warehouse_ids: [...this.state.filters.warehouse_ids] };
        try { sessionStorage.setItem(this.storageKey, JSON.stringify({ filters, tab })); } catch { /* optional */ }
        try {
            const data = await this.orm.call(MODEL, 'get_dashboard', [tab, filters]);
            if (this.alive && request === this.requestId) {
                this.state.data = data;
                this.loadedFilters = filters;
                this.state.options.today = data.today;
            }
        } catch (error) {
            if (this.alive && request === this.requestId) {
                this.state.error = this.errorMessage(error);
                this.hideOnAccessError(error);
            }
        } finally {
            if (this.alive && request === this.requestId) this.state.loading = false;
        }
    }

    setPreset(preset) {
        const today = this.state.options.today;
        this.state.preset = preset;
        this.state.filters.date_to = today;
        this.state.filters.date_from = preset === 'mes' ? `${today.slice(0, 7)}-01` : shiftDate(today, 1 - Number(preset));
        this.load();
    }

    changeDate(field, event) {
        this.state.filters[field] = event.target.value;
        this.state.preset = 'custom';
        // The displayed data must never carry newly edited but unapplied filters.
        this.requestId++;
        this.detailId++;
        this.state.detailLoading = false;
        this.state.loading = false;
        this.state.data = null;
        this.state.error = '';
    }

    changeFilter(field, event) {
        const raw = event.target.value;
        this.changeDate(field, { target: { value: field === 'comparison' ? raw : Number(raw) } });
        this.state.customerPage = 0;
    }
    toggleWarehouse(id, checked) {
        const ids = this.state.filters.warehouse_ids.filter(value => value !== id);
        if (checked) ids.push(id);
        this.changeDate('warehouse_ids', { target: { value: ids } });
    }
    clearFilters() {
        Object.assign(this.state.filters, { warehouse_ids: [], customer_id: 0, product_id: 0, family_id: 0, seller_id: 0 });
        this.load();
    }
    async searchCatalog(kind, query) {
        const request = (this.catalogRequests[kind] || 0) + 1;
        this.catalogRequests[kind] = request;
        try {
            const rows = await this.orm.call(MODEL, 'get_filter_options', [kind, query, this.state.filters[`${kind}_id`]]);
            if (this.alive && this.catalogRequests[kind] === request) this.state.options[`${kind}s`] = rows;
        } catch (error) { this.notification.add(this.errorMessage(error), { type: 'danger' }); }
    }
    get activeFilters() {
        if (!this.state.options) return [];
        const f = this.state.filters;
        const labels = f.warehouse_ids.map(id => id === 0 ? 'Sin asignar' : this.state.options.warehouses.find(w => w.id === id)?.name || `Almacén ${id}`);
        for (const filter of this.filterCatalogs) {
            if (f[`${filter.key}_id`]) labels.push(`${filter.label.split(' · ')[0]}: ${this.state.options[`${filter.key}s`].find(r => r.id === f[`${filter.key}_id`])?.name || f[`${filter.key}_id`]}`);
        }
        return labels.length ? labels : ['Todos los almacenes · incluye Sin asignar'];
    }
    get charts() { return this.state.data?.executive ? commercialCharts(this.state.data, this.state.tab) : []; }
    get productionCharts() { return this.state.data?.executive ? productionCharts(this.state.data) : []; }
    get customers() {
        return (this.state.data?.customers?.rows || []).filter(row => (!this.state.customerStatus || row.status === this.state.customerStatus) &&
            row.label.toLocaleLowerCase('es').includes(this.state.customerSearch.toLocaleLowerCase('es')));
    }
    get customerRows() { return this.customers.slice(this.state.customerPage * 25, (this.state.customerPage + 1) * 25); }
    get heatRows() { return [...(this.state.data?.customers?.rows || [])].filter(r => r.months.length).sort((a, b) => b.current - a.current).slice(0, 15); }
    heatClass(value) {
        const values = this.heatRows.flatMap(r => r.months.map(m => Math.abs(m.value)));
        const max = Math.max(1, ...values);
        return value === 0 ? 'heat_0' : value < 0 ? 'heat_negative' : value / max > .5 ? 'heat_3' : value / max > .15 ? 'heat_2' : 'heat_1';
    }
    changeCustomerList(field, value) { this.state[field] = value; this.state.customerPage = 0; }
    money(value) {
        if (value === null || value === undefined) return '—';
        return `${new Intl.NumberFormat('es-MX', { maximumFractionDigits: this.state.data.currency_digits ?? 2 }).format(value)} ${this.state.data.currency}`;
    }
    signed(value, digits = this.state.data?.currency_digits ?? 2) {
        return `${value > 0 ? '+' : ''}${new Intl.NumberFormat('es-MX', { maximumFractionDigits: digits }).format(value)}`;
    }
    variation(row) { return row.percent === null ? 'Sin base anterior' : `${row.percent > 0 ? '↑ Aumento' : row.percent < 0 ? '↓ Caída' : '= Sin cambio'} ${new Intl.NumberFormat('es-MX', { maximumFractionDigits: 2 }).format(Math.abs(row.percent))}%`; }

    number(value) { return value === null ? 'Sin base' : numberFormat.format(value); }
    scopeLabel(scope) { return scope === 'periodo' ? 'Período seleccionado' : 'Situación actual'; }
    barWidth(row, panel) { return `${Math.min(100, Math.abs(row.value) / panel.max * 100)}%`; }

    async openAction(action) {
        if (!action) return;
        try { await this.action.doAction(action); }
        catch (error) { this.notification.add(this.errorMessage(error), { type: 'danger' }); }
    }

    async showDetail(metric) {
        if (!metric.available || this.state.detailLoading) return;
        const request = ++this.detailId;
        this.state.detailLoading = true;
        const filters = { ...this.loadedFilters };
        try {
            const detail = await this.orm.call(MODEL, 'get_detail', [metric.key, filters]);
            if (!this.alive || request !== this.detailId) return;
            this.dialog.add(AnalyticsDetail, {
                detail,
                openAction: (action) => this.openAction(action),
                openRecord: (id) => this.openAction({
                    type: 'ir.actions.act_window', res_model: detail.model, res_id: id,
                    views: [[false, 'form']], context: { allowed_company_ids: [this.state.options.company_id],
                        create: false, edit: false, delete: false },
                }),
            });
        } catch (error) {
            if (this.alive && request === this.detailId) {
                if (error.data?.name === 'odoo.exceptions.AccessError') this.hideOnAccessError(error);
                else this.notification.add(this.errorMessage(error), { type: 'danger' });
            }
        } finally {
            if (this.alive && request === this.detailId) this.state.detailLoading = false;
        }
    }
}

registry.category('actions').add('hexagonos_analytics.dashboard', HexagonosAnalytics);
