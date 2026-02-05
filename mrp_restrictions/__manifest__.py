# -*- coding: utf-8 -*-
{
    'name': "MRP Restrictions",
    'summary': """
        Restringe edición de consumos en órdenes de fabricación y notifica cambios""",
    'description': """
        Este módulo:
        1. Restringe la edición de los campos 'Por consumir' y 'Consumido' en órdenes de fabricación
        2. Permite configurar una lista de empleados autorizados para realizar estas modificaciones
        3. Envía notificaciones a usuarios configurados cuando se realizan cambios en estos campos
    """,
    'author': "CIP Group",
    'website': "http://www.cip-group.com",
    'category': 'Manufacturing',
    'version': '17.0.1.0.0',
    'depends': ['mrp', 'hr', 'mail', 'stock'],
    'data': [
        'security/ir.model.access.csv',
        'views/res_config_settings_views.xml',
        'views/mrp_production_views.xml',
        'views/mrp_consumption_notification_template.xml',
    ],
    'assets': {},
    'installable': True,
    'application': False,
    'license': 'LGPL-3',
}
