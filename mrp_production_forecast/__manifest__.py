# -*- coding: utf-8 -*-
{
    'name': 'Pronóstico de Producción',
    'version': '18.0.1.0.0',
    'category': 'Manufacturing',
    'summary': 'Calcula la cantidad máxima fabricable según disponibilidad de materiales',
    'description': """
        Módulo de pronóstico de producción que calcula cuántos productos finales
        se pueden fabricar basándose en la disponibilidad real o pronosticada
        de materiales, con explosión recursiva de BoMs multinivel.
    """,
    'author': 'Custom',
    'depends': ['mrp', 'stock', 'product'],
    'data': [
        'security/ir.model.access.csv',
        'views/production_forecast_menu.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'mrp_production_forecast/static/src/**/*',
        ],
    },
    'installable': True,
    'application': False,
    'auto_install': False,
    'license': 'LGPL-3',
}
