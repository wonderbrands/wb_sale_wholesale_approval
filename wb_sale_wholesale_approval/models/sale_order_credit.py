# -*- coding: utf-8 -*-
from odoo import models, fields, api

class SaleOrder(models.Model):
    _inherit = 'sale.order'

    data_is_credit_sale = fields.Boolean(
        string='Venta a crédito',
        default=False,
        help='Marcar si esta orden se pagará usando crédito del cliente.'
    )

    data_credit_amount = fields.Monetary(
        string='Pago con crédito',
        currency_field='currency_id'
    )

    # ------------------------- Datos congelados -----------------------
    data_partner_credit_approved = fields.Boolean(
        string='Crédito aprobado (Histórico)',
        help='Indica si el cliente tenía el crédito aprobado al momento de procesar esta venta.',
        store=True,
        readonly=True
    )

    data_partner_credit_limit_amount = fields.Monetary(
        string='Límite de crédito (Histórico)',
        help='El límite de crédito total que tenía el cliente al momento de esta venta.',
        currency_field='currency_id',
        store=True,
        readonly=True
    )

    data_partner_credit_available_amount = fields.Monetary(
        string='Crédito disponible (Histórico)',
        help='El saldo a favor que tenía disponible el cliente al momento exacto de esta venta.',
        currency_field='currency_id',
        store=True,
        readonly=True
    )

    # ------------------------- Calculados ----------------------------------------------
    data_debit_amount = fields.Monetary(
        string='Pago de contado',
        currency_field='currency_id',
        compute='_compute_credit_split',
        store=False,
        readonly=True
    )

    data_total_order_amount = fields.Monetary(
        string='Total de la orden',
        currency_field='currency_id',
        compute='_compute_credit_split',
        store=False,
        readonly=True
    )

    # =======================================================================
    # LÓGICA DE CÁLCULO
    # =======================================================================
    
    # Se eliminó _compute_partner_credit_snapshot para evitar OOM con el ORM.
    # Los campos snapshot se llenarán desde action_confirm.

    @api.depends('amount_total', 'data_credit_amount', 'data_is_credit_sale')
    def _compute_credit_split(self):
        for order in self:
            total = order.amount_total
            credit = order.data_credit_amount if order.data_is_credit_sale else 0.0
            credit = max(0.0, min(credit, total))
            order.data_total_order_amount = total
            order.data_debit_amount = total - credit

    @api.onchange('data_is_credit_sale', 'data_credit_amount')
    def _onchange_credit_amount(self):
        for order in self:
            if not order.data_is_credit_sale:
                order.data_credit_amount = 0.0
            else:
                if order.data_credit_amount is None or order.data_credit_amount < 0.0:
                    order.data_credit_amount = 0.0
                if order.amount_total and order.data_credit_amount > order.amount_total:
                    order.data_credit_amount = order.amount_total

    @api.constrains('data_credit_amount', 'data_is_credit_sale')
    def _check_credit_amount_not_over_total(self):
        from odoo.exceptions import ValidationError
        for order in self:
            if not order.data_is_credit_sale:
                continue

            if order.data_credit_amount > order.amount_total + 1e-6:
                raise ValidationError("El pago con crédito no puede ser mayor al total de la orden.")
            if order.data_credit_amount < -1e-6:
                raise ValidationError("El pago con crédito no puede ser negativo.")

            if order.state in ['draft', 'sent']:
                live_available = order.partner_id.data_credit_available
                if order.data_credit_amount > live_available:
                    raise ValidationError(
                        f"El cliente no tiene suficiente crédito disponible para esta operación.\n"
                        f"Monto solicitado: ${order.data_credit_amount}\n"
                        f"Crédito disponible real: ${live_available}"
                    )
                    
    data_use_automated_credit = fields.Boolean(
        related='company_id.data_use_automated_credit',
        string="Usa cálculo automático"
    )