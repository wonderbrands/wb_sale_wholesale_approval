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
        ('validation', 'En validación'),
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
    def action_set_to_receipt_received(self):
        self.ensure_one()
        # Solo se puede pasar a 'recibido' desde 'pendiente'
        if self.data_finance_approval_status in ['pending']:

            # -------- Buscar y marcar la actividad de pago como hecha -----------------------------

            # Referencia al tipo de actividad
            activity_type_id = self.env.ref('mail.mail_activity_data_todo').id
            # Referencia al modelo de la orden de venta
            sale_order_model_id = self.env.ref('sale.model_sale_order').id

            # Buscar la actividad con el dominio
            activities_to_done = self.env['mail.activity'].search([
                ('res_id', '=', self.id),
                ('res_model_id', '=', sale_order_model_id),
                ('activity_type_id', '=', activity_type_id),
                ('summary', '=', 'Revisión de aprobación financiera')
            ])

            # Marcar las actividades encontradas como hechas
            if activities_to_done:
                activities_to_done.action_done()


            # ----------------------------------------------------------------------
            carrier = self.carrier_selection_relational

            if carrier and carrier.name == 'Pick Up': # Pickup
                self.write({
                    'data_finance_approval_status': 'validation',
                    'yuju_carrier_tracking_ref': 'Pick-up',
                    'data_total_carrier_tracking': 1,
                    #'channel_order_reference': 1, # Ejemplo para local (No hay campo total de guias)
                })
            elif not carrier:
                commercial_group = self.env.ref('wb_sale_wholesale_approval.group_sales_commercial_user')
                commercial_user = self.env['res.users'].search([('groups_id', 'in', commercial_group.ids)], limit=1)
                date_deadline_carrier = datetime.now() + timedelta(hours=72)

                # Crear actividad para comercial - asignacion de carrier y guia
                if commercial_user:
                    self.activity_schedule(
                        'mail.mail_activity_data_todo',
                        summary=_('Selección de carrier y generación de guía'),
                        note=_('Favor de seleccionar carrier y generar la guía para esta orden.'),
                        user_id=commercial_user.id,
                        date_deadline=date_deadline_carrier,
                    )
                    self.write({'data_finance_approval_status': 'validation', })
                else:
                    self.activity_schedule(
                        'mail.mail_activity_data_todo',
                        summary=_('Selección de carrier y generación de guía'),
                        note=_('Favor de seleccionar carrier y generar la guía para esta orden.'),
                        user_id=self.env.user.id,
                        date_deadline=date_deadline_carrier,
                    )
                    self.write({'data_finance_approval_status': 'validation', })

            else:
                self.write({'data_finance_approval_status': 'validation',})


    def action_set_to_collected(self):
        self.ensure_one()
        if self.data_finance_approval_status in ['validation']:
            self.write({'data_finance_approval_status': 'collected'})

    def action_set_to_rejected(self):
        self.ensure_one()
        if self.data_finance_approval_status in ['validation']:

            # Cerrar actividades pendientes relacionadas con esta orden
            activities_to_done = self.env['mail.activity'].search([
                ('res_id', '=', self.id),
                ('res_model', '=', 'sale.order'),
            ])
            if activities_to_done:
                activities_to_done.action_done()

            # Validar que no haya fecha efectiva y que el estado WMS no sea Despachado
            if not self.effective_date and self.wms_status != 'DESP':
                # Cancelar la orden de venta
                self.action_cancel()
                self.message_post(
                    body=_("La orden de venta ha sido cancelada debido al rechazo del pago.")
                )
            else:
                # Si no cumple condiciones, solo dejar el estado financiero en 'rejected'
                self.message_post(
                    body=_(
                        "El pago ha sido rechazado, pero la orden no fue cancelada porque ya tiene fecha efectiva y está despachada en WMS.")
                )

            self.write({'data_finance_approval_status': 'rejected'})

    # -------------------------------------------------------------------------------------------
    # Sobreescribir el método de confirmación
    def action_confirm(self):
        res = super(SaleOrder, self).action_confirm()
        if self.data_is_wholesale_sale:
            finance_group = self.env.ref('wb_sale_wholesale_approval.group_finance_user')
            finance_user = self.env['res.users'].search([('groups_id', 'in', finance_group.ids)], limit=1)

            self.data_confirmation_date = datetime.now()
            self.data_finance_approval_status = 'pending'
            date_deadline = datetime.now() + timedelta(hours=72)

            if finance_user:
                self.activity_schedule(
                    'mail.mail_activity_data_todo',
                    summary=_('Revisión de aprobación financiera'),
                    note=_('Revisar y aprobar el estado financiero de esta orden de venta al mayoreo.'),
                    user_id=finance_user.id,
                    date_deadline=date_deadline,
                )
            else:
                self.activity_schedule(
                    'mail.mail_activity_data_todo',
                    summary=_('Revisión de aprobación financiera'),
                    note=_('Revisar y aprobar el estado financiero de esta orden de venta al mayoreo.'),
                    user_id=self.env.user.id,
                    date_deadline=date_deadline,
                )

            # -----------------------------------------------------------------------------
            # Se suscribe a los usuarios de los grupos 'Ventas mayoreo',  'Finanzas' y Comercial a las ventas de mayoreo.
            try:
                wholesale_group = self.env.ref('wb_sale_wholesale_approval.group_sales_wholesale_user')
                finance_group = self.env.ref('wb_sale_wholesale_approval.group_finance_user')
                comercial_group = self.env.ref('wb_sale_wholesale_approval.group_sales_commercial_user')

            except ValueError:
                return

            users_to_follow = self.env['res.users'].search([
                ('groups_id', 'in', [wholesale_group.id, finance_group.id, comercial_group.id])
            ])

            partner_ids = users_to_follow.mapped('partner_id').ids
            self.message_subscribe(partner_ids=partner_ids)

        return res

    # -------------------------------------------------------------------------------------------
    # Sobreescribir el método de cancelar
    def action_cancel(self):
        for order in self:
            if order.data_is_wholesale_sale:
                # Cerrar actividades pendientes
                activities_to_done = self.env['mail.activity'].search([
                    ('res_id', '=', order.id),
                    ('res_model', '=', 'sale.order'),
                ])

                if activities_to_done:
                    activities_to_done.action_done()

                # Limpiar estado financiero
                order.data_finance_approval_status = False

        return super(SaleOrder, self).action_cancel()


    # -------------------------------------------------------------------------------------------
    # Sobreescribir el método de escritura
    def write(self, vals):
        # Verificacion de equipo de ventas 'Team_Mayoreo' para edicion con Write
        if vals.get('data_is_wholesale_sale'):
            team_mayoreo = self.env['crm.team'].search([('name', '=', 'Team_Mayoreo')], limit=1)
            if team_mayoreo:
                vals['team_id'] = team_mayoreo.id

        if self.data_is_wholesale_sale:
            if 'carrier_selection_relational' in vals and vals['carrier_selection_relational']:
                for order in self:
                    carrier_activities = self.env['mail.activity'].search([
                        ('res_id', '=', order.id),
                        ('res_model', '=', 'sale.order'),
                        ('summary', '=', 'Selección de carrier y generación de guía')
                    ])
                    if carrier_activities:
                        carrier_activities.action_done()

        return super().write(vals)

    # --------------------------------------------------------------------------------
    # Si es mayoreo, asignar team_id 'Team_Mayoreo' -  Reiteramos que es este equipo de ventas porque lo modifica al crear el record
    @api.model
    def create(self, vals):
        if vals.get('data_is_wholesale_sale'):
            team_mayoreo = self.env['crm.team'].search([('name', '=', 'Team_Mayoreo')], limit=1)
            if team_mayoreo:
                vals['team_id'] = team_mayoreo.id
        return super().create(vals)

    # -----------------------------------------------------------------------------------
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
        limit_date = datetime.now() - timedelta(hours=144)

        # Busca las órdenes que cumplen las condiciones:
        domain = [
            ('data_is_wholesale_sale', '=', True),  # Venta al mayoreo
            ('data_finance_approval_status', '=', 'pending'),  # Sigue con estado financiero 'Pendiente de Pago'
            ('state', 'in', ['sale', 'done']),  # Estado de la orden 'Orden de vcenta' o 'Bloqueado'
            ('data_confirmation_date', '<', limit_date.strftime('%Y-%m-%d %H:%M:%S'))
            # Ordenes con mas de 144 horas de confirmadas
        ]
        old_orders = self.env['sale.order'].search(domain)
        _logger.info("Se encontraron %d órdenes antiguas que serán canceladas.", len(old_orders))

        # Cancela las órdenes encontradas
        for order in old_orders:
            # Cancela la orden
            # Logica de cerrar actividades y status financiero a False, estan de action_cancel de este script
            order.action_cancel()
            order.message_post(
                body="La orden de venta ha sido cancelada automáticamente por superar el plazo de 6 días sin confirmación de pago.")
            _logger.info("La orden de venta %s ha sido cancelada.", order.name)
        _logger.info("El cron de cancelación de órdenes ha finalizado.")

    # ----------------------------------------------------------------------------------
    # Lógica para el aviso en el chatter de órdenes pendientes
    @api.model
    def _cron_send_payment_reminder_message(self):
        _logger.info("El cron de aviso 'pago pendiente ventas mayoreo' se está ejecutando.")

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
                _logger.info("Se envió un aviso para la orden %s, remitente: %s.", order.name,
                             order.user_id.name)
            else:
                _logger.warning("No se encontró un vendedor asignado para la orden %s. No se pudo enviar el aviso.",
                                order.name)

        _logger.info("El cron de aviso 'pago pendiente ventas mayoreo' ha finalizado.")