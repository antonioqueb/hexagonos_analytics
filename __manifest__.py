{
    'name': 'Hexágonos Analytics',
    'version': '18.0.1.0.0',
    'summary': 'Indicadores de dirección para manufactura, calidad y servicio al cliente',
    'category': 'Manufacturing',
    'author': 'Alphaqueb Consulting SAS',
    'license': 'LGPL-3',
    'depends': [
        'web', 'mrp', 'purchase_stock', 'restricciones_entregas',
        'quality_management', 'custom_purchase_extension',
        'control_costos_hexagonos', 'entregas_evidencias', 'empleados_hmx',
    ],
    'data': ['security/analytics_security.xml', 'views/analytics_views.xml'],
    'assets': {
        'web.assets_backend': [
            'hexagonos_analytics/static/src/analytics.js',
            'hexagonos_analytics/static/src/analytics.xml',
            'hexagonos_analytics/static/src/analytics.scss',
        ],
    },
    'application': True,
    'installable': True,
}
