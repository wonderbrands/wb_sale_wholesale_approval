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

    # ------------------------- Datos congelados al bloquear o cancelar orden -----------------------
    data_partner_credit_approved = fields.Boolean(
        string='Crédito aprobado (Histórico)',
        help='Indica si el cliente tenía el crédito aprobado al momento de procesar esta venta.',
        compute='_compute_partner_credit_snapshot',
        store=True,
        readonly=True
    )

    data_partner_credit_limit_amount = fields.Monetary(
        string='Límite de crédito (Histórico)',
        help='El límite de crédito total que tenía el cliente al momento de esta venta.',
        currency_field='currency_id',
        compute='_compute_partner_credit_snapshot',
        store=True,
        readonly=True
    )

    data_partner_credit_available_amount = fields.Monetary(
        string='Crédito disponible (Histórico)',
        help='El saldo a favor que tenía disponible el cliente al momento exacto de esta venta.',
        currency_field='currency_id',
        compute='_compute_partner_credit_snapshot',
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
    # LÓGICA DE CÁLCULO Y CONGELAMIENTO
    # =======================================================================
    @api.depends('partner_id.data_credit_approved', 'partner_id.data_credit_limit', 'partner_id.data_credit_available', 'state', 'locked','data_use_automated_credit')
    def _compute_partner_credit_snapshot(self):
        """
        Actúa como un 'related' mientras la orden está en borrador.
        Si la orden se bloquea (locked=True) o se cancela, conserva su valor histórico.
        """
        for order in self:
            is_frozen = order.locked or order.state == 'cancel'

            if is_frozen:
                #Se reasigna su propio valor actual. Así se congela para siempre en la BD, para la orden
                order.data_partner_credit_approved = order.data_partner_credit_approved
                order.data_partner_credit_limit_amount = order.data_partner_credit_limit_amount
                order.data_partner_credit_available_amount = order.data_partner_credit_available_amount
            else:
                #Si no está bloqueada, copiamos los datos en vivo del cliente
                order.data_partner_credit_approved = order.partner_id.data_credit_approved
                order.data_partner_credit_limit_amount = order.partner_id.data_credit_limit or 0.0
                order.data_partner_credit_available_amount = order.partner_id.data_credit_available or 0.0

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

            if order.data_credit_amount > order.amount_total + 1e-6:
                raise ValidationError("El pago con crédito no puede ser mayor al total de la orden.")
            if order.data_credit_amount < -1e-6:
                raise ValidationError("El pago con crédito no puede ser negativo.")

            #Se valida siempre calculando la deuda en TIEMPO REAL en la base de datos.
            if order.state in ['draft', 'sent']:
                
                live_available = order.partner_id.data_credit_available
                
                if order.data_credit_amount > live_available:
                    raise ValidationError(
                        f"El cliente no tiene suficiente crédito disponible para esta operación.\n"
                        f"Monto solicitado: ${order.data_credit_amount}\n"
                        f"Crédito disponible real: ${live_available}"
                    )
                    
    # --------------------------------------------------------------------------------------------------------------------
    data_use_automated_credit = fields.Boolean(
        related='company_id.data_use_automated_credit',
        string="Usa cálculo automático"
    )