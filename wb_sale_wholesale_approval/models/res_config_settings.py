# -*- coding: utf-8 -*-
from odoo import models, fields


class ResCompany(models.Model):
    _inherit = 'res.company'

    data_use_automated_credit = fields.Boolean(
        string="Fórmula Estricta de Crédito", 
        default=False
    )

    data_require_partner_contact_data = fields.Boolean(
        string="Datos de contacto obligatorios",
        default=False,
        help="Si está activo, al crear un contacto raíz (empresa o individuo sin contacto "
             "relacionado) serán obligatorios el teléfono, el correo electrónico, el país "
             "y el código postal."
    )

class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    data_use_automated_credit_calculation = fields.Boolean(
        related='company_id.data_use_automated_credit',
        readonly=False,
        string="Fórmula Estricta de Crédito",
        help="Si está activo, el crédito disponible restará la deuda y pedidos en tránsito. Si está inactivo, será igual al límite de crédito."
    )

    data_require_partner_contact_data = fields.Boolean(
        related='company_id.data_require_partner_contact_data',
        readonly=False,
        string="Datos de contacto obligatorios",
        help="Exige teléfono, correo electrónico, país y código postal al crear un contacto nuevo."
    )
