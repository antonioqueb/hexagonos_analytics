/** @odoo-module **/

import { registry } from '@web/core/registry';
import { useService } from '@web/core/utils/hooks';
import { Dialog } from '@web/core/dialog/dialog';
import { Component, onWillStart, onWillUnmount, useState } from '@odoo/owl';

const MODEL = 'hexagonos.analytics';
const numberFormat = new Intl.NumberFormat('es-MX', { maximumFractionDigits: 2 });

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
            error: '', preset: 'mes', detailLoading: false,
            filters: { date_from: '', date_to: '', company_id: 0 },
        });
        this.presets = [{ key: 'mes', label: 'Este mes' }, { key: '30', label: '30 días' },
            { key: '90', label: '90 días' }, { key: '365', label: '365 días' }];
        onWillStart(async () => {
            // Fail before mounting if permission was revoked or a direct URL is used.
            // Odoo handles the AccessError; no empty dashboard is rendered.
            const options = await this.orm.call(MODEL, 'get_options', []);
            if (!this.alive) return;
            this.state.options = options;
            this.state.filters = { date_from: options.date_from, date_to: options.today,
                company_id: options.company_id };
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
        this.state.loading = true;
        this.state.error = '';
        this.state.data = null;
        const filters = { ...this.state.filters };
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

    changeCompany(event) {
        this.state.filters.company_id = Number(event.target.value);
        this.load();
    }

    number(value) { return value === null ? 'Sin base' : numberFormat.format(value); }
    scopeLabel(scope) { return scope === 'periodo' ? 'Período seleccionado' : 'Situación actual'; }
    barWidth(row, panel) { return `${Math.min(100, Math.abs(row.value) / panel.max * 100)}%`; }

    async openAction(action) {
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
                    views: [[false, 'form']], context: { allowed_company_ids: [filters.company_id],
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
