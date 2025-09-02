# -*- coding: utf-8 -*-
from odoo import models, fields, api
from odoo.exceptions import ValidationError


class SaleOrder(models.Model):
    _inherit = 'sale.order'

    # Es venta con crédito?
    data_is_credit_sale = fields.Boolean(
        string='Venta a crédito',
        default=False,
        help='Marcar si esta orden se pagará usando crédito del cliente.'
    )

    # Monto que el cliente desea cubrir con crédito
    data_credit_amount = fields.Monetary(
        string='Pago con crédito',
        currency_field='currency_id'
    )

    # ------------------------- Calculados ----------------------------------------------
    data_debit_amount = fields.Monetary(
        string='Pago con débito',
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

    # Mostrar el límite de crédito del cliente
    data_partner_credit_limit_amount = fields.Monetary(
        string='Límite de crédito (cliente)',
        currency_field='currency_id',
        compute='_compute_partner_credit_info',
        store=False,
        readonly=True
    )

    @api.depends('partner_id')
    def _compute_partner_credit_info(self):
        """El límite del cliente siempre está en la moneda de la compañía."""
        for order in self:
            partner_limit = order.partner_id.data_credit_limit or 0.0
            order.data_partner_credit_limit_amount = partner_limit

    @api.depends('amount_total', 'data_credit_amount', 'data_is_credit_sale')
    def _compute_credit_split(self):
        for order in self:
            total = order.amount_total
            credit = order.data_credit_amount if order.data_is_credit_sale else 0.0
            # Ajustar dentro de los rangos válidos
            credit = max(0.0, min(credit, total))
            order.data_total_order_amount = total
            order.data_debit_amount = total - credit

    @api.onchange('data_is_credit_sale', 'data_credit_amount')
    def _onchange_credit_amount(self):
        """Normaliza en la vista: sin negativos ni montos mayores al total."""
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
        """Valida reglas de negocio al guardar."""
        for order in self:
            if not order.data_is_credit_sale:
                continue

            # Solo lo pongo por contingencia, pero se manejan en '_onchange_credit_amount'
            if order.data_credit_amount > order.amount_total + 1e-6:
                raise ValidationError("El pago con crédito no puede ser mayor al total de la orden.")
            if order.data_credit_amount < -1e-6:
                raise ValidationError("El pago con crédito no puede ser negativo.")


            partner_limit = order.partner_id.data_credit_limit or 0.0
            if order.data_credit_amount > partner_limit:
                raise ValidationError(
                    f"El pago con crédito no puede superar el límite del cliente ({partner_limit})."
                )
