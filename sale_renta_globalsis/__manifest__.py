{
    'name': 'Sale Renta Globalsis',
    'version': '18.0.1.0.0',
    'category': 'Sales',
    'summary': 'Agrega días de renta a cotizaciones y órdenes de venta',
    'description': """
        Módulo que permite configurar días de renta en las órdenes de venta.
        El importe se calcula como: Cantidad × Días de renta × Precio unitario por día.
    """,
    'author': 'Globalsis',
    'website': '',
    'license': 'LGPL-3',
    'depends': ['sale'],
    'data': [
        'views/sale_order_views.xml',
    ],
    'installable': True,
    'application': False,
    'auto_install': False,
}
