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
        compute='_compute_wholesale_status_display',)

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

            carrier = self.carrier_selection_relational

            # -------------------------------------------------
            if carrier and carrier.name == 'Pick up': # Pickup
                self.write({
                    'data_finance_approval_status': 'received',
                    'yuju_carrier_tracking_ref': 'Pick-up',
                    'data_total_carrier_tracking': 1,
                    #'channel_order_reference': 1, # Ejemplo para local (No hay campo total de guias)
                })
            else:
                self.write({'data_finance_approval_status': 'received',})

            # -------- Buscar y marcar la actividad como hecha -----------------------------

            # Referencia al tipo de actividad
            activity_type_id = self.env.ref('mail.mail_activity_data_todo').id
            # Referencia al modelo de la orden de venta
            sale_order_model_id = self.env.ref('sale.model_sale_order').id

            # Buscar la actividad con el dominio
            activities_to_done = self.env['mail.activity'].search([
                ('res_id', '=', self.id),
                ('res_model_id', '=', sale_order_model_id),
                ('activity_type_id', '=', activity_type_id)
            ])

            # Marcar las actividades encontradas como hechas
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
        if self.data_finance_approval_status in ['validation', 'partially_collected']:
            self.write({'data_finance_approval_status': 'rejected'})

    # Sobreescribir el método de confirmación
    def action_confirm(self):
        res = super(SaleOrder, self).action_confirm()
        if self.data_is_wholesale_sale:
            self.data_confirmation_date = datetime.now()
            self.data_finance_approval_status = 'pending'
            date_deadline = datetime.now() + timedelta(hours=72)
            self.activity_schedule(
                'mail.mail_activity_data_todo',
                summary=_('Revisión de aprobación financiera'),
                note=_('Revisar y aprobar el estado financiero de esta orden de venta al mayoreo.'),
                user_id=self.env.user.id,
                date_deadline=date_deadline,
            )

            # -----------------------------------------------------------------------------
            # Se suscribe a los usuarios de los grupos 'Ventas mayoreo' y 'Finanzas' a las ventas de mayoreo.
            try:
                wholesale_group = self.env.ref('wb_sale_wholesale_approval.group_sales_wholesale_user')
                finance_group = self.env.ref('wb_sale_wholesale_approval.group_finance_user')
            except ValueError:
                return

            users_to_follow = self.env['res.users'].search([
                ('groups_id', 'in', [wholesale_group.id, finance_group.id])
            ])

            partner_ids = users_to_follow.mapped('partner_id').ids
            self.message_subscribe(partner_ids=partner_ids)



        return res

    # --------------------------------------------------------------------------------
    @api.onchange('data_is_wholesale_sale')
    def _onchange_data_is_wholesale_sale(self):
        if self.data_is_wholesale_sale:
            team_mayoreo = self.env['crm.team'].search([('name', '=', 'Team_Mayoreo')], limit=1)
            if team_mayoreo:
                self.team_id = team_mayoreo.id

            almacen_general = self.env['stock.warehouse'].search([('name', '=', 'Almacen General')], limit=1)
            if almacen_general:
                self.warehouse_id = almacen_general.id
        else:
            pass

    # ----------------------------------------------------------------------------------

    # Lógica para la cancelación automática después de 144 horas
    @api.model
    def _cron_auto_cancel_old_orders(self):
        """
        Cancela automáticamente las órdenes de venta al mayoreo que han
        superado las 144 horas desde su confirmación.
        """
        _logger.info("El cron de cancelación de órdenes se está ejecutando.")

        # Define la fecha límite: hace 144 horas (6 días)
        limit_date = datetime.now() - timedelta(minutes=10)

        # Busca las órdenes que cumplen las condiciones:
        domain = [
            ('data_is_wholesale_sale', '=', True), # Venta al mayoreo
            ('data_finance_approval_status', '=', 'pending'), # Sigue con estado financiero 'Pendiente de Pago'
            ('state', 'in', ['sale', 'done']), # Estado de la orden 'Orden de vcenta' o 'Bloqueado'
            ('data_confirmation_date', '<', limit_date.strftime('%Y-%m-%d %H:%M:%S')) # Ordenes con mas de 144 horas de confirmadas
        ]

        old_orders = self.env['sale.order'].search(domain)

        _logger.info("Se encontraron %d órdenes antiguas que serán canceladas.", len(old_orders))

        # Cancela las órdenes encontradas
        for order in old_orders:
            order.action_cancel()
            order.message_post(
                body="La orden de venta ha sido cancelada automáticamente por superar el plazo de 6 días sin confirmación de pago.")
            _logger.info("La orden de venta %s ha sido cancelada.", order.name)

        _logger.info("El cron de cancelación de órdenes ha finalizado.")

    # ----------------------------------------------------------------------------------
    # Lógica para el aviso en el chatter de órdenes pendientes
    @api.model
    def _cron_send_payment_reminder_message(self):
        _logger.info("El cron de aviso en el chatter se está ejecutando.")

        activity_type_id = self.env.ref('mail.mail_activity_data_todo').id
        now = datetime.now()

        overdue_activities = self.env['mail.activity'].search([
            ('res_model_id', '=', self.env.ref('sale.model_sale_order').id),
            ('activity_type_id', '=', activity_type_id),
            ('date_deadline', '<', now)
        ])

        orders_to_remind = self.env['sale.order'].search([
            ('id', 'in', overdue_activities.mapped('res_id')),
            ('state', 'in', ['sale', 'done']),
            ('data_is_wholesale_sale', '=', True),
            ('data_finance_approval_status', '=', 'pending'),
        ])

        _logger.info("Se encontraron %d órdenes de venta que necesitan un aviso de pago.", len(orders_to_remind))

        for order in orders_to_remind:
            message_body = "El pago de esta orden de venta al mayoreo está vencido. Por favor, revísalo y actualiza el estado financiero."

            # Obtiene el ID del vendedor asignado a la orden
            if order.user_id:
                author_id = order.user_id.partner_id.id
                order.message_post(
                    body=message_body,
                    message_type='comment',
                    subtype_xmlid='mail.mt_comment',
                    author_id=author_id
                )
                _logger.info("Se envió un aviso en el chatter para la orden %s, remitente: %s.", order.name,
                             order.user_id.name)
            else:
                _logger.warning("No se encontró un vendedor asignado para la orden %s. No se pudo enviar el aviso.",
                                order.name)

        _logger.info("El cron de aviso en el chatter ha finalizado.")

