# -*- coding: utf-8 -*-
from odoo import models, fields


class CarriersList(models.Model):
    """Marca los carriers que corresponden a logística propia.

    Se hereda el modelo en lugar de modificar WB_data_sale_order, de modo que la
    casilla viaje con este módulo y sobreviva a las actualizaciones del otro.
    """
    _inherit = 'carriers.list'

    data_is_internal_logistics = fields.Boolean(
        string='Logística interna o Pick Up',
        default=False,
        help='Marcar en los carriers que no son paquetería externa (Pick Up, '
             'Logística Interna). En las ventas al mayoreo, la actividad de guía '
             'de estos carriers se asigna al responsable de logística interna '
             'en lugar del responsable de paquetería.'
    )
