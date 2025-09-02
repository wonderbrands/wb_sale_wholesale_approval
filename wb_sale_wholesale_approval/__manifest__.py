{
    'name': 'Aprobación de Ventas al Mayoreo',
    'license': 'LGPL-3',
    'version': '1.0.0',
    'summary': 'Módulo para gestionar la aprobación financiera de ventas al mayoreo.',
    'description': """
        Este módulo extiende el modelo de ventas de Odoo para permitir una gestión
        centralizada y auditable de los pedidos de ventas al por mayor,
        incluyendo la aprobación financiera.
    """,
    'author': 'Sergio Gil Guerrero García',
    'category': 'Sales',
    'depends': [
        'sale',
        'sale_management',
        'stock'
    ],
    'data': [
        'security/sales_wholesale_groups.xml', # antes de las vistas
        'security/ir.model.access.csv',
        'views/sale_order_views.xml',
        'views/res_partner_views.xml',
        'views/sale_order_credit_views.xml',
        'data/ir_cron.xml',
    ],
    'installable': True,
    'application': False,
    'auto_install': False,
}
