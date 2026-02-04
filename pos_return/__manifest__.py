# -*- coding: utf-8 -*-
{
    'name': 'POS Devoluciones',
    'version': '19.0.1.0.0',
    'category': 'Point of Sale',
    'summary': 'Gestión de devoluciones de mercancía sin registro de venta',
    'description': """
        Este módulo permite registrar devoluciones de mercancía 
        que no tienen un registro de venta en Odoo.
        
        Funcionalidades:
        - Nueva acción "Devolución" en el menú del POS
        - Campo para número de ticket (Arus u otros)
        - Creación automática de recepción de inventario
        - Registro de salida de efectivo en la sesión POS
        
        Migración v18 -> v19:
        - Compatibilidad verificada con OWL v2+
        - APIs de stock.move y account.bank.statement.line revisadas
        - Sin uso de APIs deprecadas
    """,
    'author': 'dataliza',
    'website': 'https://www.dataliza.com',
    'depends': [
        'point_of_sale',
        'stock',
    ],
    'data': [
        'security/ir.model.access.csv',
    ],
    'assets': {
        'point_of_sale._assets_pos': [
            'pos_return/static/src/**/*',
        ],
    },
    'installable': True,
    'application': False,
    'auto_install': False,
    'license': 'LGPL-3',
}