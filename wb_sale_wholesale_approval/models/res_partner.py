# -*- coding: utf-8 -*-
from odoo import models, fields, api

class ResPartner(models.Model):
    _inherit = 'res.partner'

    # crédito aprobado?
    data_credit_approved = fields.Boolean(
        string='Crédito aprobado',
        default=False,
        help='Indica si este contacto tiene crédito autorizado.'
    )

    # Moneda del límite de crédito (la default)
    data_credit_currency_id = fields.Many2one(
        'res.currency',
        string='Moneda del crédito',
        related='company_id.currency_id',
        readonly=True,
        store=True
    )

    # Límite de crédito en moneda de la compañía
    data_credit_limit = fields.Monetary(
        string='Límite de crédito',
        currency_field='data_credit_currency_id',
        default=0.0,
        help='Monto máximo de crédito autorizado para este contacto.'
    )

    # control de edit del campo 'data_credit_limit'
    can_edit_credit_limit = fields.Boolean(
        string='Puede editar límite de crédito',
        compute='_compute_can_edit_credit_limit',
        readonly=True
    )

    @api.depends('data_credit_approved')
    def _compute_can_edit_credit_limit(self):
        for partner in self:
            partner.can_edit_credit_limit = (
                    partner.data_credit_approved and
                    self.env.user.has_group('wb_sale_wholesale_approval.group_finance_user')
            )
