{
    'name': 'Hexágonos Analytics',
    'version': '18.0.2.0.5',
    'summary': 'Dirección comercial, recurrencia de clientes y producción por almacén',
    'category': 'Manufacturing',
    'author': 'Alphaqueb Consulting SAS',
    'license': 'LGPL-3',
    'depends': [
        'web', 'mrp', 'sale_stock', 'account', 'purchase_stock', 'restricciones_entregas',
        'quality_management', 'custom_purchase_extension',
        'control_costos_hexagonos', 'entregas_evidencias', 'empleados_hmx',
    ],
    'data': ['security/analytics_security.xml', 'views/analytics_views.xml'],
    'assets': {
        'web.assets_backend': [
            'hexagonos_analytics/static/src/mixed.js',
            'hexagonos_analytics/static/src/charts.js',
            'hexagonos_analytics/static/src/analytics.js',
            'hexagonos_analytics/static/src/analytics.xml',
            'hexagonos_analytics/static/src/executive.xml',
            'hexagonos_analytics/static/src/analytics.scss',
        ],
    },
    'application': True,
    'installable': True,
}
