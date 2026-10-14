# -*- coding: utf-8 -*-
from odoo import models, fields, api, _
from odoo.exceptions import ValidationError

class SaleOrder(models.Model):
    _inherit = 'sale.order'

    # Es venta con crédito?
    data_is_credit_sale = fields.Boolean(
        string='Venta a crédito',
        default=False,
        index=True,  # lo filtra el cálculo de crédito disponible
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

    # ------------------------- Presentación en el formulario ---------------------------
    # Los campos *_amount de arriba son el histórico congelado y solo tienen valor
    # a partir de la confirmación. Para que el vendedor siga viendo el crédito del
    # cliente mientras cotiza, estos campos muestran el dato vivo en borrador y el
    # congelado una vez confirmada la orden.
    # Son NO almacenados y dependen del cliente en sentido directo, así que no
    # reintroducen la cascada de recálculos que causaba el problema de rendimiento.
    data_partner_credit_approved_display = fields.Boolean(
        string='Crédito aprobado',
        compute='_compute_partner_credit_display',
    )

    data_partner_credit_limit_display = fields.Monetary(
        string='Límite de crédito',
        currency_field='currency_id',
        compute='_compute_partner_credit_display',
    )

    data_partner_credit_available_display = fields.Monetary(
        string='Crédito disponible',
        currency_field='currency_id',
        compute='_compute_partner_credit_display',
    )

    @api.depends(
        'state',
        'partner_id.data_credit_approved',
        'partner_id.data_credit_limit',
        'partner_id.data_credit_available',
        'data_partner_credit_approved',
        'data_partner_credit_limit_amount',
        'data_partner_credit_available_amount',
    )
    def _compute_partner_credit_display(self):
        for order in self:
            if order.state in ('draft', 'sent'):
                partner = order.partner_id
                order.data_partner_credit_approved_display = partner.data_credit_approved
                order.data_partner_credit_limit_display = partner.data_credit_limit or 0.0
                order.data_partner_credit_available_display = partner.data_credit_available or 0.0
            else:
                order.data_partner_credit_approved_display = order.data_partner_credit_approved
                order.data_partner_credit_limit_display = order.data_partner_credit_limit_amount
                order.data_partner_credit_available_display = order.data_partner_credit_available_amount

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

    def _wb_invoiced_unpaid_ratio(self):
        """Proporción del importe facturado de la orden que sigue sin pagarse.

        Devuelve 1.0 cuando no hay nada cobrado y 0.0 cuando está todo saldado.
        Se trabaja con la razón residual/total y no con importes absolutos
        porque una misma factura puede cubrir varias órdenes: aplicar la
        proporción de cobro reparte el abono de forma razonable, mientras que
        repartir importes exigiría imputar línea por línea.

        Las notas de crédito restan con signo negativo. Solo se consideran las
        facturas publicadas: una en borrador no ha cobrado nada.
        """
        self.ensure_one()

        invoices = self.invoice_ids.filtered(lambda move: move.state == 'posted')
        if not invoices:
            return 1.0

        total = 0.0
        residual = 0.0
        for invoice in invoices:
            sign = 1.0 if invoice.move_type == 'out_invoice' else -1.0
            total += sign * invoice.amount_total
            residual += sign * invoice.amount_residual

        if not total:
            return 1.0

        return min(1.0, max(0.0, residual / total))

    # =======================================================================
    # LÓGICA DE CÁLCULO
    # =======================================================================
    # Los tres campos de arriba son almacenados SIN compute. Antes los llenaba
    # _compute_partner_credit_snapshot, que dependía de campos del cliente, de
    # state y de locked: eso obligaba al ORM a recalcular y reescribir el
    # snapshot en TODAS las órdenes de la base ante cualquier cambio, y provocaba
    # agotamiento de memoria en procesos masivos.
    # Ahora se escriben una sola vez, explícitamente, al confirmar la orden
    # (ver _wb_freeze_partner_credit_snapshot en sale_order.py).

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

    @api.constrains('data_credit_amount', 'data_is_credit_sale', 'amount_total')
    def _check_credit_amount_not_over_total(self):
        """Valida reglas de negocio al guardar."""
        for order in self:
            if not order.data_is_credit_sale:
                continue

            if order.data_credit_amount > order.amount_total + 1e-6:
                raise ValidationError("El pago con crédito no puede ser mayor al total de la orden.")
            if order.data_credit_amount < -1e-6:
                raise ValidationError("El pago con crédito no puede ser negativo.")

            # Se valida calculando la deuda en TIEMPO REAL en la base de datos.
            # Aplica a las cotizaciones y también a las órdenes confirmadas que
            # siguen desbloqueadas, porque ahí el monto a crédito es editable.
            is_editable = order.state in ('draft', 'sent') or (
                order.state == 'sale' and not order.locked
            )
            if is_editable:
                # Se excluye la propia orden del cálculo: si ya está confirmada,
                # su importe actual forma parte de la deuda en tránsito y, sin
                # excluirlo, se compararía contra sí mismo y cualquier edición
                # fallaría. En una cotización la exclusión no tiene efecto,
                # porque todavía no cuenta como tránsito.
                live_available = order.partner_id._wb_credit_available_excluding(order)

                if order.data_credit_amount > live_available:
                    raise ValidationError(_(
                        "El cliente no tiene suficiente crédito disponible para esta operación.\n"
                        "Monto solicitado: %(requested)s\n"
                        "Crédito disponible real: %(available)s",
                        requested=order.data_credit_amount,
                        available=live_available,
                    ))
                    
    # --------------------------------------------------------------------------------------------------------------------
    data_use_automated_credit = fields.Boolean(
        related='company_id.data_use_automated_credit',
        string="Usa cálculo automático"
    )