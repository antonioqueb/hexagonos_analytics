"""Synthetic RPC payloads for visual verification, never installed as dashboard data."""


def fixtures():
    def panel(key, title, labels, model='quality.inspection', unit='', dimension=None, values=None):
        rows = []
        for index, label in enumerate(labels):
            action = dict(type='ir.actions.act_window', res_model=model, domain=[('company_id', '=', 1), ('id', '=', index + 1)],
                          views=[[False, 'list']], context={'allowed_company_ids': [1]})
            rows.append(dict(key=str(index), id=index + 1, label=label, value=(values or [8, 4, 2, 1])[index],
                action=action, chart_code=f'QA-{index + 1:03d}', chart_name=label))
        return dict(key=key, label=title, scope='periodo', available=True, note='Datos sintéticos de prueba.',
                    unit=unit, dimension=dimension, rows=rows)

    result = {
        'calidad': [panel('quality_state', 'Estado actual de las inspecciones del período', ['Aceptado', 'Rechazado', 'Retenido']),
                    panel('quality_process', 'Retenciones por proceso', ['Proceso de recubrimiento', 'Proceso de laminado']),
                    panel('quality_plant', 'Retenciones por planta declarada', ['Planta A', 'Planta B']),
                    panel('quality_reject_process', 'Rechazos por proceso', ['Proceso de recubrimiento', 'Proceso de laminado'])],
        'entregas': [panel('delivery_customer', 'Líneas vencidas por cliente', ['CLIENTE CON UN NOMBRE COMERCIAL MUY LARGO PARA VERIFICAR EL DISEÑO', 'Cliente de prueba'], 'sale.order.line', dimension='customer'),
                     panel('delivery_warehouse', 'Líneas vencidas por almacén', ['Almacén A', 'Almacén B'], 'sale.order.line'),
                     panel('delivery_product', 'Compromisos próximos por producto', ['PRODUCTO DE RECUBRIMIENTO INDUSTRIAL CON NOMBRE MUY LARGO', 'Producto de prueba'], 'sale.order.line', dimension='product'),
                     panel('dispatch_warehouse', 'Entregas realizadas por almacén de despacho', ['Almacén A', 'Almacén B'], 'stock.picking')],
        'compras': [panel('purchase_type', 'Compras por tipo de gasto', ['Materia prima', 'Servicio'], 'purchase.order'),
                    panel('purchase_plant', 'Compras por planta declarada', ['Planta A', 'Planta B'], 'purchase.order'),
                    panel('purchase_supplier', 'Compras por proveedor', ['Proveedor de prueba A', 'Proveedor de prueba B'], 'purchase.order'),
                    panel('receipt_supplier', 'Recepciones vencidas por proveedor', ['Proveedor A', 'Proveedor B'], 'stock.picking'),
                    panel('purchase_amount', 'Compra confirmada por moneda', ['MXN', 'USD'], 'purchase.order', unit='moneda', values=[25000, 1200])],
        'inventario': [panel('negative_location', 'Posiciones negativas por ubicación', ['Almacén A / Existencias', 'Almacén B / Existencias'], 'stock.quant'),
                       panel('stock_units', 'Existencia y reserva por unidad', ['Piezas', 'kg'], 'stock.quant', values=[100, 25]),
                       panel('scrap_units', 'Desecho registrado por unidad', ['Piezas', 'kg'], 'stock.scrap', unit='cantidad')],
        'documentos': [panel('document_state', 'Pendientes documentales por estado', ['Sin evidencia', 'En revisión'], 'delivery.evidence.invoice')],
        'personas': [panel('attendance_plant', 'Registros por planta de captura', ['Planta A', 'Planta B'], 'hmx.attendance'),
                     panel('attendance_incidence', 'Registros por incidencia', ['Asistencia', 'Falta', 'Permiso'], 'hmx.attendance'),
                     panel('attendance_cross', 'Resultado del cruce', ['Coincide', 'Sin checada', 'Jornada corta'], 'hmx.attendance')],
    }
    for row in result['inventario'][1]['rows']:
        row.update(reserved=10, free=row['value'] - 10)
    return {key: dict(metrics=[], panels=panels, today='2026-09-16', company='EMPRESA DE PRUEBA',
                     period='2026-09-01 — 2026-09-16', updated_at='16/09/2026 12:00',
                     scope_note='Fixtures sintéticos para revisión visual.') for key, panels in result.items()}
