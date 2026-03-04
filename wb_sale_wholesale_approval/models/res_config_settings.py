# -*- coding: utf-8 -*-
from odoo import models, fields


class ResCompany(models.Model):
    _inherit = 'res.company'

    data_use_automated_credit = fields.Boolean(
        string="Fórmula Estricta de Crédito", 
        default=False
    )
    
class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    data_use_automated_credit_calculation = fields.Boolean(
        related='company_id.data_use_automated_credit',
        readonly=False,
        string="Fórmula Estricta de Crédito",
        help="Si está activo, el crédito disponible restará la deuda y pedidos en tránsito. Si está inactivo, será igual al límite de crédito."
    )