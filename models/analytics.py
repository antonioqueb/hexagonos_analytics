"""Read-only analytics. Every aggregate and drill uses the caller's ORM rules."""
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from odoo import api, fields, models, _
from odoo.exceptions import AccessError, ValidationError


TABS = [
    ('resumen', 'Dirección'), ('produccion', 'Producción'),
    ('calidad', 'Calidad'), ('entregas', 'Entregas'),
    ('comercial', 'Comercial'), ('compras', 'Abastecimiento'),
    ('inventario', 'Inventario'), ('documentos', 'Evidencias'),
    ('personas', 'Asistencia'),
]
TZ = 'America/Monterrey'
OPEN_MO = ['confirmed', 'progress', 'to_close']
OPEN_PICKING = ['draft', 'waiting', 'confirmed', 'assigned']
DISCREPANCIES = ['sin_checada', 'checo_con_incidencia', 'sin_captura',
                 'incompleta', 'jornada_corta', 'planta_distinta']


def utc_midnight(day):
    return datetime.combine(day, time.min, tzinfo=ZoneInfo(TZ)).astimezone(
        timezone.utc).replace(tzinfo=None)


class HexagonosAnalytics(models.AbstractModel):
    _name = 'hexagonos.analytics'
    _description = 'Indicadores de dirección de Hexágonos'

    def _check_access(self):
        if not self.env.user.has_group('hexagonos_analytics.group_analytics_viewer'):
            raise AccessError(_('No tiene acceso a Analytics Hexágonos.'))

    def _scope(self, filters):
        self._check_access()
        if not isinstance(filters, dict):
            raise ValidationError(_('Los filtros no son válidos.'))
        today = fields.Date.context_today(self.with_context(tz=TZ))
        try:
            start = fields.Date.to_date(filters.get('date_from') or today.replace(day=1))
            end = fields.Date.to_date(filters.get('date_to') or today)
            company_id = int(filters.get('company_id') or self.env.company.id)
        except (TypeError, ValueError, OverflowError):
            raise ValidationError(_('Revise las fechas y la compañía seleccionada.'))
        if start > end or (end - start).days > 365 or end > today:
            raise ValidationError(_('Seleccione hasta 366 días, sin fechas futuras.'))
        if company_id not in self.env.companies.ids:
            raise AccessError(_('La compañía no está habilitada en su sesión.'))
        service = self.with_context(allowed_company_ids=[company_id], tz=TZ)
        return service, {'date_from': start, 'date_to': end, 'today': today,
                         'company_id': company_id}

    @api.model
    def get_options(self):
        self._check_access()
        today = fields.Date.context_today(self.with_context(tz=TZ))
        return {
            'tabs': [{'key': key, 'label': label} for key, label in TABS],
            'companies': [{'id': c.id, 'name': c.name} for c in self.env.companies],
            'company_id': self.env.company.id, 'today': str(today),
            'date_from': str(today.replace(day=1)), 'timezone': TZ,
        }

    def _catalog(self, f):
        """One definition is used for each value, formula, chart and drill domain."""
        company = f['company_id']
        day = fields.Datetime.to_string(utc_midnight(f['today']))
        soon = fields.Datetime.to_string(utc_midnight(f['today'] + timedelta(days=8)))
        now = fields.Datetime.to_string(fields.Datetime.now())
        specs = {}

        def period(field, date_only=False):
            if date_only:
                return [(field, '>=', str(f['date_from'])), (field, '<=', str(f['date_to']))]
            return [(field, '>=', fields.Datetime.to_string(utc_midnight(f['date_from']))),
                    (field, '<', fields.Datetime.to_string(utc_midnight(f['date_to'] + timedelta(days=1))))]

        def add(key, tab, label, model, domain, definition, *, scope='actual',
                company_field='company_id', summary=False, tone='neutral',
                date_field='create_date', denominator=None, aggregate=None, unit='', group=None):
            base = [(company_field, '=', company)]
            specs[key] = dict(key=key, tab=tab, label=label, model=model,
                              domain=base + domain, definition=definition, scope=scope,
                              summary=summary, tone=tone, date_field=date_field,
                              denominator=base + denominator if denominator is not None else None,
                              aggregate=aggregate, unit=unit, group=group)

        mo = [('state', 'in', OPEN_MO)]
        done = [('state', '=', 'done')] + period('date_finished')
        add('mo_open', 'produccion', 'Órdenes de producción abiertas', 'mrp.production', mo,
            'OP confirmadas, en proceso o por cerrar de cualquier fecha. Excluye borradores y canceladas.',
            summary=True, date_field='date_deadline')
        add('mo_late', 'produccion', 'OP con fecha límite vencida', 'mrp.production',
            mo + [('date_deadline', '<', now)],
            'OP abiertas cuya fecha límite vigente ya pasó. Se compara con la hora de actualización.',
            summary=True, tone='danger', date_field='date_deadline')
        add('mo_waiting', 'produccion', 'OP en espera de materiales', 'mrp.production',
            [('state', '=', 'confirmed'), ('reservation_state', 'in', ['confirmed', 'waiting'])],
            'OP confirmadas no listas según la política de reserva de su lista de materiales. No equivale a faltante físico ni considera todas las OP en proceso.',
            tone='warning', date_field='date_start')
        add('mo_done', 'produccion', 'OP terminadas', 'mrp.production', done,
            'OP en estado Hecho, por fecha real de finalización en el período. Cuenta órdenes, no piezas.',
            scope='periodo', summary=True, date_field='date_finished')
        add('mo_on_time', 'produccion', 'Cierre dentro de fecha límite', 'mrp.production',
            done + [('hmx_deadline_result', '=', 'on_time')],
            'OP terminadas a más tardar en su fecha límite / OP terminadas con fecha límite. Usa el compromiso vigente, no una línea base congelada; excluye OP sin fecha.',
            scope='periodo', denominator=done + [('hmx_deadline_result', 'in', ['on_time', 'late'])],
            date_field='date_finished', unit='%')
        add('mo_no_date', 'produccion', 'OP abiertas sin fecha límite', 'mrp.production',
            mo + [('date_deadline', '=', False)],
            'OP abiertas sin fecha límite. No se clasifican automáticamente como puntuales.', tone='warning')
        add('mo_done_no_date', 'produccion', 'Cierres sin fecha evaluable', 'mrp.production',
            done + [('hmx_deadline_result', '=', 'no_date')],
            'OP terminadas en el período que quedan fuera del porcentaje de cumplimiento por falta de fecha límite.',
            scope='periodo', tone='warning', date_field='date_finished')

        inspection_company = 'production_order_id.company_id'
        inspections = period('date_inspection')
        add('quality_checked', 'calidad', 'Inspecciones registradas', 'quality.inspection', inspections,
            'Inspecciones por fecha de captura; su estado es el vigente al consultar. Una OP puede tener varias inspecciones y procesos.',
            scope='periodo', company_field=inspection_company, date_field='date_inspection')
        add('quality_acceptance', 'calidad', 'Aceptación de inspecciones resueltas', 'quality.inspection',
            inspections + [('state', '=', 'aceptado')],
            'Aceptadas / (aceptadas + rechazadas), entre inspecciones capturadas en el período. Excluye borradores, en proceso y retenidas. No es rendimiento a la primera ni porcentaje de piezas buenas.',
            scope='periodo', company_field=inspection_company, date_field='date_inspection',
            denominator=inspections + [('state', 'in', ['aceptado', 'rechazado'])], unit='%')
        add('quality_held', 'calidad', 'Inspecciones con retención abierta', 'quality.inspection',
            ['|', ('state', '=', 'retenido'),
             ('retention_state', 'in', ['retenido', 'en_correccion', 'correccion_hecha', 'reinspeccion'])],
            'Retenciones pendientes, incluyendo corrección y reinspección en curso. Cuenta inspecciones, no OP distintas ni unidades retenidas.',
            company_field=inspection_company, summary=True, tone='danger', date_field='date_inspection')
        add('quality_reinspect', 'calidad', 'Correcciones listas para reinspección', 'quality.inspection',
            [('retention_state', '=', 'correccion_hecha')],
            'Producción terminó la corrección y está pendiente el inicio de reinspección por Calidad.',
            company_field=inspection_company, tone='warning', date_field='date_inspection')
        add('quality_rejected', 'calidad', 'Inspecciones rechazadas', 'quality.inspection',
            inspections + [('state', '=', 'rechazado')],
            'Estado actual rechazado de las inspecciones capturadas en el período; no es merma física.',
            scope='periodo', company_field=inspection_company, date_field='date_inspection')
        add('quality_8d', 'calidad', 'Acciones correctivas abiertas', 'quality.corrective.action',
            [('state', 'in', ['evaluacion_calidad', 'abierta', 'en_proceso'])],
            'Expedientes 8D en evaluación, abiertos o en proceso. Excluye borradores, cerrados y no procedentes.',
            date_field='date_opened')

        lines = [('order_id.state', '=', 'sale'), ('display_type', '=', False),
                 ('is_downpayment', '=', False), ('product_id.type', '=', 'consu'),
                 ('qty_to_deliver_report', '>', 0)]
        add('delivery_pending', 'entregas', 'Líneas de venta por entregar', 'sale.order.line', lines,
            'Líneas de bienes en pedidos confirmados con cantidad pendiente positiva (pedido menos entregado, neto de devoluciones según Odoo). Excluye servicios y anticipos. Una línea puede tener entregas parciales.',
            summary=True, date_field='report_commitment_date')
        add('delivery_late', 'entregas', 'Líneas con entrega vencida', 'sale.order.line',
            lines + [('report_commitment_date', '<', day)],
            'Líneas pendientes con fecha efectiva anterior a hoy en Monterrey. Usa la fecha por línea para pedidos nuevos y la global para históricos, según restricciones_entregas; no usa el estatus almacenado que puede quedar desactualizado.',
            summary=True, tone='danger', date_field='report_commitment_date')
        add('delivery_soon', 'entregas', 'Compromisos de hoy y próximos 7 días', 'sale.order.line',
            lines + [('report_commitment_date', '>=', day), ('report_commitment_date', '<', soon)],
            'Líneas pendientes comprometidas entre hoy y el séptimo día siguiente, inclusive.',
            tone='warning', date_field='report_commitment_date')
        add('delivery_no_date', 'entregas', 'Líneas pendientes sin compromiso', 'sale.order.line',
            lines + [('report_commitment_date', '=', False)],
            'Pendientes sin fecha efectiva; requieren programación antes de evaluar cumplimiento.',
            tone='warning', date_field='order_date')
        outgoing = [('picking_type_code', '=', 'outgoing'), ('location_dest_id.usage', '=', 'customer')]
        add('delivery_done', 'entregas', 'Transferencias a cliente realizadas', 'stock.picking',
            outgoing + [('state', '=', 'done')] + period('date_done'),
            'Transferencias de salida finalizadas hacia ubicación cliente por fecha real. No mide pedidos completos, OTIF ni recepción firmada por el cliente.',
            scope='periodo', date_field='date_done')

        sales = [('state', '=', 'sale')] + period('date_order')
        add('sales_orders', 'comercial', 'Pedidos confirmados', 'sale.order', sales,
            'Pedidos actualmente confirmados cuya fecha de pedido pertenece al período. No representa facturación, ingresos contables ni cobros.',
            scope='periodo', date_field='date_order')
        add('sales_quotes', 'comercial', 'Cotizaciones abiertas', 'sale.order', [('state', 'in', ['draft', 'sent'])],
            'Cotizaciones vigentes en borrador o enviadas, de cualquier fecha. No son ventas confirmadas.', date_field='date_order')
        add('sales_expired', 'comercial', 'Cotizaciones con vigencia vencida', 'sale.order',
            [('state', 'in', ['draft', 'sent']), ('validity_date', '<', str(f['today']))],
            'Cotizaciones abiertas cuya fecha de vencimiento es anterior a hoy; excluye las que no tienen vigencia.',
            tone='warning', date_field='validity_date')

        purchases = [('state', 'in', ['purchase', 'done'])] + period('date_approve')
        add('purchase_orders', 'compras', 'Compras confirmadas', 'purchase.order', purchases,
            'Órdenes actualmente confirmadas o bloqueadas, por fecha de aprobación en el período. No equivale a pago ni a material recibido.',
            scope='periodo', date_field='date_approve')
        add('purchase_authorize', 'compras', 'Solicitudes sin autorización', 'purchase.order',
            [('state', 'in', ['draft', 'sent', 'to approve']), ('is_authorized', '=', False)],
            'Solicitudes abiertas con la autorización personalizada pendiente. Se usa is_authorized, no el prefijo del folio.',
            tone='warning', date_field='date_order')
        incoming = [('picking_type_code', '=', 'incoming'), ('location_id.usage', '=', 'supplier'),
                    ('state', 'in', OPEN_PICKING)]
        add('receipt_pending', 'compras', 'Recepciones de proveedor pendientes', 'stock.picking', incoming,
            'Transferencias abiertas desde proveedor. Cuenta recepciones, no líneas de compra ni disponibilidad del material.',
            date_field='scheduled_date')
        add('receipt_late', 'compras', 'Recepciones con programación vencida', 'stock.picking',
            incoming + [('scheduled_date', '<', day)],
            'Recepciones pendientes programadas antes de hoy. La programación vigente puede replanificarse; no es una promesa histórica del proveedor.',
            summary=True, tone='danger', date_field='scheduled_date')
        add('purchase_unclassified', 'compras', 'Compras sin planta o tipo', 'purchase.order',
            purchases + ['|', ('planta', 'in', [False, 'none']), ('tipo', 'in', [False, 'none'])],
            'Compras confirmadas del período sin clasificación completa de planta y tipo de gasto.',
            scope='periodo', tone='warning', date_field='date_approve')

        internal = [('location_id.usage', '=', 'internal')]
        add('inventory_positions', 'inventario', 'Posiciones con existencia positiva', 'stock.quant',
            internal + [('quantity', '>', 0)],
            'Registros de existencias por producto, ubicación, lote, paquete y propietario con cantidad positiva. No es número de productos ni unidades.',
            date_field='in_date')
        add('inventory_negative', 'inventario', 'Posiciones con existencia negativa', 'stock.quant',
            internal + [('quantity', '<', 0)],
            'Registros de inventario interno con saldo negativo. Revisar consumos, movimientos y ajustes; no se compensan con saldos de otras ubicaciones.',
            summary=True, tone='danger', date_field='in_date')
        add('inventory_reserved', 'inventario', 'Posiciones con reservas', 'stock.quant',
            internal + [('reserved_quantity', '>', 0)],
            'Posiciones internas con cantidad reservada. Las reservas no se presentan como existencias libres.', date_field='in_date')
        add('scrap_done', 'inventario', 'Registros de desecho validados', 'stock.scrap',
            [('state', '=', 'done')] + period('date_done'),
            'Desechos registrados y validados en el período. Su cantidad se desglosa por unidad; no se infiere merma a partir de rechazos ni de subproductos recuperables.',
            scope='periodo', date_field='date_done')

        add('document_pending', 'documentos', 'Facturas externas sin comprobación', 'delivery.evidence.invoice',
            [('state', 'in', ['pending', 'in_oficio', 'reopened'])],
            'Facturas importadas pendientes de comprobación documental. No representan cuentas por cobrar ni entregas físicas pendientes.', date_field='date')
        add('document_oficios', 'documentos', 'Oficios pendientes de firma', 'delivery.evidence.oficio',
            [('state', '=', 'generated')],
            'Oficios generados pendientes de comprobación mediante evidencia firmada.', tone='warning', date_field='date')
        add('document_verified', 'documentos', 'Oficios comprobados', 'delivery.evidence.oficio',
            [('state', '=', 'verified')] + period('verified_date'),
            'Oficios actualmente comprobados cuya fecha de comprobación está en el período. Excluye comprobaciones revertidas.',
            scope='periodo', date_field='verified_date')
        add('document_unmatched', 'documentos', 'Facturas sin cliente vinculado', 'delivery.evidence.invoice',
            [('state', '!=', 'cancelled'), ('partner_id', '=', False)],
            'Facturas externas activas sin vínculo a un contacto de Odoo, aunque conserven razón social en el archivo.',
            tone='warning', date_field='date')

        attendance = period('date', date_only=True)
        employee_company = 'employee_id.company_id'
        add('attendance_records', 'personas', 'Registros de asistencia e incidencia', 'hmx.attendance.record', attendance,
            'Registros empleado-fecha-planta capturados en el período. Una persona multiplanta puede aparecer varias veces; no es plantilla ni cobertura de turnos.',
            scope='periodo', company_field=employee_company, date_field='date')
        add('attendance_review', 'personas', 'Registros pendientes de validación', 'hmx.attendance.record',
            attendance + [('state', 'in', ['draft', 'to_review'])],
            'Capturas del período que todavía no han sido validadas por el responsable.',
            scope='periodo', company_field=employee_company, tone='warning', date_field='date')
        add('attendance_discrepancy', 'personas', 'Discrepancias con el checador', 'hmx.attendance.record',
            attendance + [('cross_status', 'in', DISCREPANCIES)],
            'Registros del período con discrepancia en el cruce. No equivale automáticamente a ausencia; excluye registros sin cruzar.',
            scope='periodo', company_field=employee_company, tone='warning', date_field='date')
        add('attendance_uncrossed', 'personas', 'Registros sin cruce de checador', 'hmx.attendance.record',
            attendance + [('cross_status', '=', 'pendiente')],
            'Registros aún sin conciliar con el reloj. Se muestran separados de las discrepancias confirmadas por el cruce.',
            scope='periodo', company_field=employee_company, date_field='date')
        add('attendance_overtime', 'personas', 'Horas extra capturadas y validadas', 'hmx.attendance.record',
            attendance + [('state', '=', 'validated')],
            'Suma del tiempo extra capturado en registros validados. No es tiempo productivo medido ni cálculo de nómina.',
            scope='periodo', company_field=employee_company, date_field='date', aggregate='overtime_hours', unit='h')
        return specs

    def _action(self, spec, domain=None):
        return {'type': 'ir.actions.act_window', 'name': spec['label'],
                'res_model': spec['model'], 'views': [[False, 'list'], [False, 'form']],
                'domain': spec['domain'] if domain is None else domain,
                'context': {'allowed_company_ids': self.env.companies.ids,
                            'create': False, 'edit': False, 'delete': False}}

    def _metric(self, spec):
        result = {k: spec[k] for k in ('key', 'label', 'definition', 'scope', 'tone', 'unit', 'tab')}
        result.update(available=True, value=None, sample=None, numerator=None)
        try:
            model = self.env[spec['model']]
            model.check_access_rights('read')
            if spec['aggregate']:
                result['value'] = model._read_group(spec['domain'], [], [spec['aggregate'] + ':sum'])[0][0] or 0
            else:
                count = model.search_count(spec['domain'])
                if spec['denominator'] is not None:
                    total = model.search_count(spec['denominator'])
                    result.update(value=100 * count / total if total else None,
                                  sample=total, numerator=count)
                else:
                    result['value'] = count
        except AccessError:
            result.update(available=False, reason='Sin permiso de lectura en la fuente.')
        return result

    def _group_panel(self, spec, key, label, groupby, *, measure=None, group=None):
        panel = {'key': key, 'label': label, 'scope': spec['scope'], 'rows': [],
                 'available': True, 'unit': '', 'note': 'Seleccione una fila para abrir sus registros.'}
        if group and not self.env.user.has_group(group):
            return dict(panel, available=False, note='Importes restringidos por Control de Costos.')
        model = self.env[spec['model']]
        try:
            model.check_access_rights('read')
            # read_group supplies __domain, including timezone-aware month boundaries.
            field_name = groupby.split(':')[0]
            groups = model.read_group(spec['domain'], [field_name] + ([measure] if measure else []),
                                      [groupby], lazy=False)
            field = model._fields[field_name]
            selections = dict(field._description_selection(self.env)) if field.type == 'selection' else {}
            for index, row in enumerate(groups):
                value = row.get(field_name, row.get(groupby))
                label_value = value[1] if isinstance(value, (tuple, list)) else selections.get(value, value)
                if value in (False, 'none'):
                    label_value = 'Sin clasificar'
                number = (row.get(measure) or 0) if measure else row['__count']
                panel['rows'].append({'key': str(index), 'label': str(label_value or 'Sin clasificar'),
                                      'value': number, 'action': self._action(spec, row['__domain'])})
            if ':month' not in groupby:
                panel['rows'].sort(key=lambda r: r['value'], reverse=True)
            total_groups = len(panel['rows'])
            panel['rows'] = panel['rows'][:15]
            if total_groups > 15:
                panel['note'] = 'Se muestran los 15 grupos principales de %s. El indicador incluye todos.' % total_groups
            if groupby == 'currency_id':
                panel['note'] = 'Importes sin impuestos por moneda original; no se suman ni se convierten entre monedas.'
                panel['unit'] = 'moneda'
            panel['max'] = max([abs(r['value']) for r in panel['rows']] + [1])
        except AccessError:
            panel.update(available=False, note='Sin permiso de lectura en la fuente.')
        return panel

    def _panels(self, tab, specs):
        config = {
            'resumen': [('mo_open', 'mo_state', 'Carga actual de producción', 'state'),
                        ('delivery_late', 'delivery_warehouse', 'Entregas vencidas por almacén', 'order_warehouse_id'),
                        ('mo_done', 'mo_monthly', 'OP terminadas por mes', 'date_finished:month'),
                        ('quality_held', 'quality_process', 'Retenciones abiertas por proceso', 'process_type_id')],
            'produccion': [('mo_open', 'mo_state', 'Carga actual por estado', 'state'),
                           ('mo_open', 'mo_operation', 'Carga por tipo de operación', 'picking_type_id'),
                           ('mo_done', 'mo_monthly', 'OP terminadas por mes', 'date_finished:month'),
                           ('mo_done', 'mo_product', 'Productos con más OP terminadas', 'product_id')],
            'calidad': [('quality_checked', 'quality_state', 'Estado actual de las inspecciones del período', 'state'),
                        ('quality_held', 'quality_process', 'Retenciones por proceso', 'process_type_id'),
                        ('quality_held', 'quality_plant', 'Retenciones por planta declarada', 'plant'),
                        ('quality_rejected', 'quality_reject_process', 'Rechazos por proceso', 'process_type_id')],
            'entregas': [('delivery_late', 'delivery_customer', 'Líneas vencidas por cliente', 'order_partner_id'),
                         ('delivery_late', 'delivery_warehouse', 'Líneas vencidas por almacén', 'order_warehouse_id'),
                         ('delivery_soon', 'delivery_product', 'Compromisos próximos por producto', 'product_id')],
            'comercial': [('sales_orders', 'sales_monthly', 'Pedidos confirmados por mes', 'date_order:month'),
                          ('sales_orders', 'sales_customer', 'Pedidos por cliente', 'partner_id'),
                          ('sales_orders', 'sales_warehouse', 'Pedidos por almacén', 'warehouse_id')],
            'compras': [('purchase_orders', 'purchase_type', 'Compras por tipo de gasto', 'tipo'),
                        ('purchase_orders', 'purchase_plant', 'Compras por planta declarada', 'planta'),
                        ('purchase_orders', 'purchase_supplier', 'Compras por proveedor', 'partner_id'),
                        ('receipt_late', 'receipt_supplier', 'Recepciones vencidas por proveedor', 'partner_id')],
            'inventario': [('inventory_negative', 'negative_location', 'Posiciones negativas por ubicación', 'location_id')],
            'documentos': [('document_pending', 'document_state', 'Pendientes documentales por estado', 'state')],
            'personas': [('attendance_records', 'attendance_plant', 'Registros por planta de captura', 'planta_id'),
                         ('attendance_records', 'attendance_incidence', 'Registros por incidencia', 'incidence_type_id'),
                         ('attendance_records', 'attendance_cross', 'Resultado del cruce', 'cross_status')],
        }
        panels = [self._group_panel(specs[source], key, title, field)
                  for source, key, title, field in config.get(tab, [])]
        if tab == 'comercial':
            panels.append(self._group_panel(specs['sales_orders'], 'sales_amount', 'Venta confirmada por moneda',
                                           'currency_id', measure='amount_untaxed',
                                           group='control_costos_hexagonos.group_view_sale_total'))
        if tab == 'compras':
            panels.append(self._group_panel(specs['purchase_orders'], 'purchase_amount', 'Compra confirmada por moneda',
                                           'currency_id', measure='amount_untaxed',
                                           group='control_costos_hexagonos.group_view_purchase_total'))
        if tab == 'produccion':
            # Stock movements have their own record rules and actual movement date.
            # Do not serialize ORM Query objects into client action domains.
            production_domain = [('company_id', '=', self.env.company.id), ('state', '=', 'done'),
                                 ('production_id', '!=', False), ('byproduct_id', '=', False),
                                 ('scrapped', '=', False)]
            production_domain += [('date', operator, value)
                                  for field, operator, value in specs['mo_done']['domain']
                                  if field == 'date_finished']
            panels.append(self._quantity_panel('produced', 'Producción principal registrada por unidad',
                'stock.move', production_domain,
                'product_uom', 'quantity', scope='periodo',
                note='Movimientos terminados de producción por fecha del movimiento; excluye subproductos y desechos. Puede incluir avances de OP aún abiertas. No suma unidades distintas.'))
        if tab == 'inventario':
            panels.append(self._stock_panel())
            panels.append(self._quantity_panel('scrap_units', 'Desecho registrado por unidad', 'stock.scrap',
                specs['scrap_done']['domain'], 'product_uom_id', 'scrap_qty', scope='periodo',
                note='Cantidades validadas por unidad de medida. Sin porcentaje de merma: falta una base homogénea de consumo.'))
        return panels

    def _quantity_panel(self, key, title, model, domain, uom, quantity, *, scope, note):
        spec = {'label': title, 'scope': scope, 'model': model, 'domain': domain}
        panel = self._group_panel(spec, key, title, uom, measure=quantity)
        panel['unit'] = 'cantidad'
        panel['note'] = note if panel['available'] else panel['note']
        return panel

    def _stock_panel(self):
        panel = dict(key='stock_units', label='Existencia y reserva por unidad', scope='actual',
                     rows=[], available=True, unit='',
                     note='Ubicaciones internas, sin inventario de terceros. Libre = existencia − reserva; puede ser negativo. No descuenta retenciones de Calidad ni equivale a material liberado.')
        domain = [('company_id', '=', self.env.company.id), ('location_id.usage', '=', 'internal'),
                  ('owner_id', '=', False)]
        try:
            model = self.env['stock.quant']
            model.check_access_rights('read')
            groups = model._read_group(domain, ['product_id'], ['quantity:sum', 'reserved_quantity:sum'])
            units = {}
            for product, quantity, reserved in groups:
                uom = product.uom_id
                row = units.setdefault(uom.id, dict(key=str(uom.id), label=uom.display_name,
                                                   value=0, reserved=0, free=0))
                row['value'] += quantity
                row['reserved'] += reserved
                row['free'] += quantity - reserved
            spec = {'label': panel['label'], 'model': 'stock.quant', 'domain': domain}
            for uid, row in units.items():
                row['action'] = self._action(spec, domain + [('product_id.uom_id', '=', uid)])
            panel['rows'] = list(units.values())
            panel['max'] = max([abs(r['value']) for r in panel['rows']] + [1])
        except AccessError:
            panel.update(available=False, note='Sin permiso de lectura en la fuente.')
        return panel

    @api.model
    def get_dashboard(self, tab='resumen', filters=None):
        service, parsed = self._scope(filters or {})
        if tab not in dict(TABS):
            raise ValidationError(_('Sección desconocida.'))
        specs = service._catalog(parsed)
        selected = [s for s in specs.values() if s['summary']] if tab == 'resumen' else [
            s for s in specs.values() if s['tab'] == tab]
        return {
            'metrics': [service._metric(s) for s in selected],
            'panels': service._panels(tab, specs),
            'company': service.env.company.name,
            'updated_at': fields.Datetime.context_timestamp(service, fields.Datetime.now()).strftime('%d/%m/%Y %H:%M'),
            'period': '%s — %s' % (parsed['date_from'], parsed['date_to']),
            'today': str(parsed['today']),
        }

    @api.model
    def get_detail(self, key, filters=None):
        service, parsed = self._scope(filters or {})
        spec = service._catalog(parsed).get(key)
        if not spec:
            raise ValidationError(_('Indicador desconocido.'))
        model = service.env[spec['model']]
        model.check_access_rights('read')
        domain = spec['denominator'] if spec['denominator'] is not None else spec['domain']
        fields_to_read = ['display_name', spec['date_field']]
        if 'state' in model._fields:
            fields_to_read.append('state')
        # Limit applies only to the preview; the count and action are exhaustive.
        records = model.search_read(domain, list(dict.fromkeys(fields_to_read)),
                                    limit=20, order='%s asc, id asc' % spec['date_field'])
        selection = model._fields.get('state')
        labels = dict(selection._description_selection(service.env)) if selection and selection.type == 'selection' else {}
        rows = []
        for record in records:
            date = record.get(spec['date_field'])
            if date and model._fields[spec['date_field']].type == 'datetime':
                date = fields.Datetime.context_timestamp(service, fields.Datetime.to_datetime(date)).strftime('%d/%m/%Y %H:%M')
            elif date:
                date = fields.Date.to_date(date).strftime('%d/%m/%Y')
            rows.append({'id': record['id'], 'name': record['display_name'], 'date': date or 'Sin fecha',
                         'state': labels.get(record.get('state'), record.get('state') or '')})
        return {'metric': service._metric(spec), 'rows': rows, 'model': spec['model'],
                'date_label': model._fields[spec['date_field']].string,
                'action': service._action(spec, domain), 'total': model.search_count(domain),
                'detail_note': 'Se muestran los registros del denominador.' if spec['denominator'] is not None else ''}
