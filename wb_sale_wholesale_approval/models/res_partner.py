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
    data_credit_currency_id = fields.Many2one('res.currency', string="Moneda", default=lambda self: self.env.company.currency_id.id)

    # Valor editable real (solo si está aprobado)
    data_credit_limit_raw = fields.Monetary(
        string='Editar límite de crédito',
        currency_field='data_credit_currency_id',
        default=0.0,
    )

    # Valor mostrado al usuario
    data_credit_limit = fields.Monetary(
        string='Límite de crédito',
        currency_field='data_credit_currency_id',
        compute='_compute_data_credit_limit',
        store=False,
        default=0.0,
    )

    # Campo funcional para controlar si el usuario puede editar el límite de crédito
    can_edit_credit_limit = fields.Boolean(
        string='Puede editar límite de crédito',
        compute='_compute_can_edit_credit_limit',
        readonly=True
    )

    @api.depends('data_credit_approved')
    def _compute_can_edit_credit_limit(self):
        for partner in self:
            partner.can_edit_credit_limit = (
                    partner.data_credit_approved
                    and self.env.user.has_group('wb_sale_wholesale_approval.group_finance_user')
            )

    @api.depends('data_credit_approved', 'data_credit_limit_raw')
    def _compute_data_credit_limit(self):
        for partner in self:
            if partner.data_credit_approved:
                partner.data_credit_limit = partner.data_credit_limit_raw
            else:
                partner.data_credit_limit = 0.0
