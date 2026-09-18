"""Validated official publications and analytical USD/MXN conversion (no Odoo I/O)."""
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from urllib.parse import urlencode

from .analytics_math import months_between, shift_month

DOF_BASE = 'https://sidof.segob.gob.mx/dof/sidof/indicadores/158'


class DofRateError(ValueError):
    pass


def source_url(start, end):
    return '%s/%s/%s' % (DOF_BASE, start.strftime('%d-%m-%Y'), end.strftime('%d-%m-%Y'))


def public_url(start, end):
    return 'https://www.dof.gob.mx/indicadores_detalle.php?' + urlencode({
        'cod_tipo_indicador': 158, 'dfecha': start.strftime('%d/%m/%Y'), 'hfecha': end.strftime('%d/%m/%Y')})


def monthly_publications(payload, start, end, today):
    if not isinstance(payload, dict) or str(payload.get('messageCode')) != '200':
        raise DofRateError('El DOF no devolvió una consulta válida.')
    rows = payload.get('ListaIndicadores')
    if not isinstance(rows, list) or payload.get('TotalIndicadores') != len(rows):
        raise DofRateError('La respuesta del DOF está incompleta o cambió de formato.')
    daily = {}
    for row in rows:
        try:
            if str(row['codTipoIndicador']) != '158':
                raise ValueError()
            day = datetime.strptime(row['fecha'], '%d-%m-%Y').date()
            value = Decimal(str(row['valor']))
            if not start <= day <= end or not value.is_finite() or value <= 0:
                raise ValueError()
        except (KeyError, TypeError, ValueError, InvalidOperation):
            raise DofRateError('El DOF devolvió un indicador, fecha o valor no válido.')
        if day in daily and daily[day] != value:
            raise DofRateError('El DOF devolvió valores distintos para la misma fecha.')
        daily[day] = value  # A publication date has one vote, even if repeated in the response.
    result = {}
    for month in months_between(start, end):
        first = datetime.strptime(month + '-01', '%Y-%m-%d').date()
        last = min(shift_month(first, 1) - timedelta(days=1), end)
        values = sorted((day, value) for day, value in daily.items() if day.strftime('%Y-%m') == month)
        result[month] = dict(month=month, available=bool(values), provisional=first.year == today.year and first.month == today.month,
            average=str(sum((value for _, value in values), Decimal(0)) / len(values)) if values else None,
            count=len(values), first_publication=str(values[0][0]) if values else None,
            last_publication=str(values[-1][0]) if values else None, through=str(last),
            source_url=source_url(first, last), public_url=public_url(first, last),
            publications=[dict(date=str(day), value=str(value)) for day, value in values],
            error='' if values else 'Sin publicaciones del dólar en el rango consultado.')
    return result


def converted_amount(amount, currency_id, mxn_id, usd_id, month, rates):
    value = Decimal(str(amount or 0))
    if currency_id == mxn_id:
        return value
    if currency_id != usd_id:
        raise DofRateError('El consolidado solo admite MXN y USD.')
    rate = rates.get(month)
    if not rate or not rate.get('available'):
        raise DofRateError('No hay promedio DOF verificable para %s.' % month)
    return value * Decimal(rate['average'])
