"""Cache of public DOF data, isolated from accounting exchange rates."""
from datetime import timedelta

import requests

from odoo import api, fields, models

from .analytics_math import months_between, shift_month
from .dof_math import DofRateError, monthly_publications, public_url, source_url


class DofMonth(models.Model):
    _name = 'hexagonos.analytics.dof.month'
    _description = 'Promedio mensual del dólar publicado en el DOF'
    _rec_name = 'month'
    _order = 'month desc, fetched_at desc, id desc'

    # Append-only snapshots avoid concurrent upsert races and retain the fetched evidence.
    month = fields.Char(required=True, index=True)
    fetched_at = fields.Datetime(required=True, index=True)
    through = fields.Date(required=True)
    payload = fields.Json(required=True)

    @api.model
    def _fetch(self, start, end, today):
        try:
            response = requests.get(source_url(start, end), timeout=(3.05, 12), allow_redirects=False,
                                    headers={'Accept': 'application/json', 'User-Agent': 'HexagonosAnalytics/18 DOF monthly average'})
            response.raise_for_status()
            if response.status_code != 200 or len(response.content) > 5_000_000:
                raise DofRateError('Respuesta inesperada del servicio DOF.')
            return monthly_publications(response.json(), start, end, today)
        except (requests.RequestException, ValueError) as error:
            raise DofRateError('No fue posible verificar las publicaciones del DOF. Reintente más tarde.') from error

    @api.model
    def _monthly_rates(self, start, end, today):
        start = start.replace(day=1)
        end = min(shift_month(end.replace(day=1), 1) - timedelta(days=1), today)
        months = months_between(start, end)
        now = fields.Datetime.now()
        # Only this public cache is elevated. No sale, accounting or customer query uses sudo.
        cache = self.sudo()
        latest = {}
        for row in cache.search([('month', 'in', months)], order='fetched_at desc, id desc'):
            latest.setdefault(row.month, row)
        result, pending = {}, []
        for month in months:
            row = latest.get(month)
            month_end = min(shift_month(fields.Date.to_date(month + '-01'), 1) - timedelta(days=1), today)
            ttl = timedelta(minutes=10) if row and not row.payload.get('available') else (
                timedelta(hours=6) if month == today.strftime('%Y-%m') else timedelta(days=30))
            was_open = row and row.payload.get('provisional') and month != today.strftime('%Y-%m')
            if row and not was_open and row.through >= month_end and now - row.fetched_at < ttl:
                result[month] = dict(row.payload, fetched_at=fields.Datetime.to_string(row.fetched_at))
            else:
                pending.append(month)
        # At most one request per calendar year, only when a required month needs refreshing.
        for year in sorted({month[:4] for month in pending}):
            selected = [month for month in pending if month.startswith(year)]
            low = fields.Date.to_date(selected[0] + '-01')
            high = min(shift_month(fields.Date.to_date(selected[-1] + '-01'), 1) - timedelta(days=1), today)
            try:
                values = self._fetch(low, high, today)
            except DofRateError as error:
                values = {month: dict(month=month, available=False, average=None, count=0, publications=[],
                    provisional=month == today.strftime('%Y-%m'), through=str(high), error=str(error),
                    source_url=source_url(low, high), public_url=public_url(low, high)) for month in selected}
            for month in selected:
                value = values[month]
                cache.create(dict(month=month, fetched_at=now, through=value['through'], payload=value))
                result[month] = dict(value, fetched_at=fields.Datetime.to_string(now))
        return result
