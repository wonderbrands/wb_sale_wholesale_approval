# -*- coding: utf-8 -*-
from odoo import models, fields, api, _
from datetime import datetime, timedelta
import logging

_logger = logging.getLogger(__name__)


class SaleOrder(models.Model):
    _inherit = 'sale.order'

    data_is_wholesale_sale = fields.Boolean(string='Es Venta al Mayoreo', default=False)

    data_wholesale_status_display = fields.Char(
        string='Venta al Mayoreo',
        compute='_compute_wholesale_status_display',
        store=True)

    @api.depends('data_is_wholesale_sale')
    def _compute_wholesale_status_display(self):
        for order in self:
            if order.data_is_wholesale_sale:
                order.data_wholesale_status_display = 'VENTA AL MAYOREO'
            else:
                order.data_wholesale_status_display = False

    data_finance_approval_status = fields.Selection([
        ('pending', 'Pendiente de pago'),
        ('received', 'Pago recibido'),
        ('validation', 'En validación'),
        ('partially_collected', 'Parcialmente cobrado'),
        ('collected', 'Pago cobrado'),
        ('rejected', 'Pago rechazado'),
    ], string='Estado Financiero', tracking=True, readonly=True, default=False)

    data_confirmation_date = fields.Datetime(
        string='Fecha de Confirmación SO',
        readonly=True,
        tracking=True
    )

    # --------------------------------------------------------------------------------
    # Métodos para los botones de cambio de estado
    def action_set_to_received(self):
        self.ensure_one()
        # Solo se puede pasar a 'recibido' desde 'pendiente'
        if self.data_finance_approval_status in ['pending']:
            self.write({
                'data_finance_approval_status': 'received',
                'yuju_carrier_tracking_ref': 'Guía-12345',
                #'data_total_carrier_tracking': 1,
                'channel_order_reference': 1, # Ejemplo para local (No hay campo total de guias)
            })

            # Buscar y marcar la actividad como hecha
            # Es más seguro y claro usar el método 'search' directamente en el modelo mail.activity

            # 1. Obtener la referencia al tipo de actividad
            activity_type_id = self.env.ref('mail.mail_activity_data_todo').id
            # 2. Obtener la referencia al modelo de la orden de venta
            sale_order_model_id = self.env.ref('sale.model_sale_order').id

            # 3. Buscar la actividad con el dominio correcto
            activities_to_done = self.env['mail.activity'].search([
                ('res_id', '=', self.id),
                ('res_model_id', '=', sale_order_model_id),
                ('activity_type_id', '=', activity_type_id)
            ])

            # 4. Marcar las actividades encontradas como hechas
            if activities_to_done:
                activities_to_done.action_done()

    def action_set_to_validation(self):
        self.ensure_one()
        if self.data_finance_approval_status in ['received']:
            self.write({'data_finance_approval_status': 'validation'})

    def action_set_to_partially_collected(self):
        self.ensure_one()
        if self.data_finance_approval_status in ['validation']:
            self.write({'data_finance_approval_status': 'partially_collected'})

    def action_set_to_collected(self):
        self.ensure_one()
        if self.data_finance_approval_status in ['validation', 'partially_collected']:
            self.write({'data_finance_approval_status': 'collected'})

    def action_set_to_rejected(self):
        self.ensure_one()
        if self.data_finance_approval_status in ['validation']:
            self.write({'data_finance_approval_status': 'rejected'})

    # Sobreescribir el método de confirmación
    def action_confirm(self):
        res = super(SaleOrder, self).action_confirm()
        if self.data_is_wholesale_sale:
            self.data_confirmation_date = datetime.now()
            self.data_finance_approval_status = 'pending'
            date_deadline = datetime.now() + timedelta(minutes=2)#timedelta(hours=144)
            self.activity_schedule(
                'mail.mail_activity_data_todo',
                summary=_('Revisión de aprobación financiera'),
                note=_('Revisar y aprobar el estado financiero de esta orden de venta al mayoreo.'),
                user_id=self.env.user.id,
                date_deadline=date_deadline,
            )
        return res

    # --------------------------------------------------------------------------------
    # Lógica para la cancelación automática usando la fecha de la actividad
    @api.model
    def _cron_auto_cancel_unpaid_orders(self):
        """
        Cancela automáticamente las órdenes de venta al mayoreo cuya actividad
        de aprobación de pago ha vencido.
        """
        _logger.info("El cron de cancelación automática se está ejecutando.")

        # Obtiene el ID del tipo de actividad "Por hacer"
        activity_type_id = self.env.ref('mail.mail_activity_data_todo').id

        # Busca las actividades de "Revisión de aprobación financiera" que están vencidas
        # sin usar 'activity_state' para compatibilidad con Odoo 15.
        # En su lugar, comparamos la fecha de vencimiento con la fecha actual.
        now = datetime.now()
        overdue_activities = self.env['mail.activity'].search([
            ('res_model_id', '=', self.env.ref('sale.model_sale_order').id),
            ('activity_type_id', '=', activity_type_id),
            ('date_deadline', '<', now)
        ])

        _logger.info("Se encontraron %d actividades vencidas.", len(overdue_activities))

        # Obtiene las órdenes asociadas a las actividades vencidas
        expired_orders = self.env['sale.order'].search([
            ('id', 'in', overdue_activities.mapped('res_id')),
            ('state', '=', 'sale'),
            ('data_is_wholesale_sale', '=', True),
            ('data_finance_approval_status', '=', 'pending'),
        ])

        _logger.info("Se encontraron %d órdenes de venta que cumplen los criterios de cancelación.",
                     len(expired_orders))

        # Cancela las órdenes encontradas
        for order in expired_orders:
            order.action_cancel()
            order.message_post(
                body="La orden de venta ha sido cancelada automáticamente por no recibir la aprobación de pago a tiempo.")
            _logger.info("La orden de venta %s ha sido cancelada.", order.name)

        _logger.info("El cron de cancelación automática ha finalizado.")
