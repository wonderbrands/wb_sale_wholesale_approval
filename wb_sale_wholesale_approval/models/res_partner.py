# -*- coding: utf-8 -*-
from odoo import models, fields, api
import logging

_logger = logging.getLogger(__name__)


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
        'res.currency', string="Moneda", 
        default=lambda self: self.env.company.currency_id.id
    )

    # Valor editable real (solo si está aprobado)
    data_credit_limit_raw = fields.Monetary(
        string='Editar límite de crédito',
        currency_field='data_credit_currency_id',
        default=0.0,
    )

    # Valor mostrado al usuario (El tope máximo)
    data_credit_limit = fields.Monetary(
        string='Límite de crédito',
        currency_field='data_credit_currency_id',
        compute='_compute_data_credit_limit',
        store=False,
        default=0.0,
    )


    data_credit_available = fields.Monetary(
        string='Crédito Disponible',
        currency_field='data_credit_currency_id',
        compute='_compute_credit_available',
        help="Límite de crédito menos la deuda actual (facturas no pagadas + órdenes en tránsito)."
    )

    # Campo funcional para controlar si el usuario puede editar el límite de crédito
    can_edit_credit_limit = fields.Boolean(
        string='Puede editar límite de crédito',
        compute='_compute_can_edit_credit_limit',
        readonly=True
    )

    @api.depends('data_credit_approved')
    @api.depends_context('uid')
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

    # ====================================================================
    # LÓGICA DE CÁLCULO DEL CRÉDITO DISPONIBLE
    # ====================================================================
    #Depende de campo nativo de Odoo 'credit' (Cuentas por cobrar)
    # y de las órdenes de venta que NO se han facturado aun.
    @api.depends(
        'data_credit_limit', 
        'credit', 
        'company_id.data_use_automated_credit'
    )
    def _compute_credit_available(self):
        for partner in self:
            partner.data_credit_available = 0.0

            if not partner.data_credit_approved or partner.data_credit_limit <= 0:
                continue

            #leer ajuste directo de company
            use_automated = self.env.company.data_use_automated_credit

            if not use_automated:
                #MODO MANUAL
                partner.data_credit_available = partner.data_credit_limit
                continue

            #MODO AUTOMÁTICO: Fórmula Estricta
            invoiced_debt = partner.credit or 0.0

            #Extraemos el ID
            real_partner_id = partner.commercial_partner_id.id or partner._origin.id or partner.id
            if not real_partner_id:
                partner.data_credit_available = partner.data_credit_limit
                continue


            domain = [
                ('partner_id', 'child_of', real_partner_id),
                ('state', '=', 'sale'), 
                ('invoice_status', 'in', ['to invoice', 'no']), 
                ('data_is_credit_sale', '=', True)
            ]
            
            # Agregación en SQL (read_group): NO materializamos el recordset de
            # orders en la caché de Python, evitando el pico de RAM al descargar
            # el commit en procesos masivos (validación de lotes de stock).
            transit_rows = self.env['sale.order'].read_group(
                domain,
                ['data_credit_amount:sum'],
                ['partner_id'],
            )
            transit_debt = sum((row['data_credit_amount'] or 0.0) for row in transit_rows)

#ecuación:
            #credito_disponible = limite_credito - facturas_sin_pagar - debito_en_transito
            remaining_credit = partner.data_credit_limit - invoiced_debt - transit_debt
            
            partner.data_credit_available = max(0.0, remaining_credit)

    # --------------------------------------------------------------------
    # Refresco puntual y barato del crédito disponible.
    # Después de que el flujo de mayoreo cambia una SO (confirmar / cobrar /
    # rechazar / cancelar), se llama a este método sobre el partner para
    # recalcular data_credit_available sin depender de que Odoo tenga que
    # resolver la relación inversa masiva sale_order_ids en el write path.
    # --------------------------------------------------------------------
    def _refresh_wholesale_credit_available(self):
        if not self:
            return self
        for partner in self.sudo().exists():
            partner._compute_credit_available()
        return self