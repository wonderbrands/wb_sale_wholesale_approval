# -*- coding: utf-8 -*-
from odoo import models, fields, api, _
from odoo.exceptions import UserError, ValidationError
from odoo.tools import float_compare
from collections import defaultdict
from datetime import datetime, timedelta
import logging

_logger = logging.getLogger(__name__)


class SaleOrder(models.Model):
    _inherit = 'sale.order'

    # index: lo filtran los dos crons, el cálculo de crédito y los filtros de búsqueda.
    data_is_wholesale_sale = fields.Boolean(
        string='Es Venta al Mayoreo', default=False, index=True)

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
        ('pending', 'Pendiente de comprobante'),
        ('validation', 'En validación'),
        ('collected', 'Pago cobrado'),
        ('rejected', 'Pago rechazado'),
    ], string='Estado Financiero', tracking=True, readonly=True, default=False)

    data_confirmation_date = fields.Datetime(
        string='Fecha de Confirmación SO',
        readonly=True,
        tracking=True
    )

    # ================================================================================
    # ESTADO "APARTADO"
    # ================================================================================
    # Paso OPCIONAL del flujo de mayoreo. La orden se confirma con el método nativo
    # -reservando stock y generando los movimientos de almacén- y queda marcada como
    # apartada hasta que Finanzas registre el cobro.
    #
    # El campo nativo `state` NUNCA toma el valor 'apartado': para Odoo, para el
    # almacén (wmds), para los conectores (madkting/yuju) y para el autofacturador
    # la orden es una venta confirmada normal. El estado apartado vive únicamente en
    # `data_sale_state`, que es el campo que debe consumir el reporting de mayoreo.
    #
    # Apartar no es obligatorio: el botón Confirmar nativo sigue disponible y deja la
    # orden directamente en estado de venta, como cualquier otro pedido.

    def _wb_sale_state_selection(self):
        """Selección nativa de `state` con 'apartado' insertado antes de 'sale'.

        Las etiquetas se leen del propio campo nativo, así los rótulos y sus
        traducciones son exactamente los que Odoo muestra en el idioma del usuario
        (Cotización / Cotización enviada / Orden de venta / Cancelado).
        """
        native = self.env['sale.order']._fields['state']._description_selection(self.env)
        selection = []
        for value, label in native:
            if value == 'sale':
                selection.append(('apartado', _("Apartado")))
            selection.append((value, label))
        if 'apartado' not in [value for value, _label in selection]:
            # Salvaguarda por si el core dejara de exponer el valor 'sale'.
            selection.append(('apartado', _("Apartado")))
        return selection

    data_is_reserved = fields.Boolean(
        string='Apartado',
        default=False,
        copy=False,
        readonly=True,
        tracking=True,
        index=True,
        help='La orden está confirmada y con stock reservado, pero el pago aún no '
             'ha sido validado por Finanzas.'
    )

    data_sale_state = fields.Selection(
        selection=lambda self: self._wb_sale_state_selection(),
        string='Estado de venta',
        compute='_compute_data_sale_state',
        store=True,
        index=True,
        copy=False,
        help='Estado del pedido para las ventas al mayoreo. Coincide con el estado '
             'nativo salvo cuando la orden está apartada. Es el campo que debe usar '
             'el reporting de mayoreo.'
    )

    @api.depends('state', 'data_is_reserved')
    def _compute_data_sale_state(self):
        for order in self:
            if order.data_is_reserved and order.state == 'sale':
                order.data_sale_state = 'apartado'
            else:
                order.data_sale_state = order.state

    def _wb_check_stock_availability(self):
        """Valida que haya stock libre suficiente para apartar la orden.

        Se compara contra free_qty (existencia física menos lo ya reservado por
        otras órdenes) en el almacén de la orden, que es exactamente lo que el
        apartado va a tomar. No se usa la cantidad prevista porque incluiría
        mercancía en tránsito que todavía no se puede apartar.

        Solo se invoca desde el botón Apartar: el flujo nativo de Odoo no pasa
        por aquí.
        """
        self.ensure_one()

        lines = self.order_line.filtered(
            lambda line: not line.display_type and line.product_id.is_storable
        )
        if not lines:
            return

        # Se acumula por producto: el mismo artículo puede venir en varias líneas,
        # y la unidad de medida se normaliza a la de referencia del producto.
        demand = defaultdict(float)
        for line in lines:
            demand[line.product_id] += line.product_uom._compute_quantity(
                line.product_uom_qty, line.product_id.uom_id
            )

        warehouse = self.warehouse_id

        # free_qty se lee sobre el recordset completo, no producto por producto:
        # el cálculo de existencias se resuelve en lote y pasa de una consulta
        # por línea a una sola, sin importar el tamaño de la orden.
        products = self.env['product.product'].browse([product.id for product in demand])
        if warehouse:
            products = products.with_context(warehouse_id=warehouse.id)
        free_qty_by_product = {product.id: product.free_qty for product in products}

        shortages = []
        for product, qty_needed in demand.items():
            available = free_qty_by_product.get(product.id, 0.0)
            if float_compare(qty_needed, available, precision_rounding=product.uom_id.rounding) > 0:
                shortages.append((product, qty_needed, available))

        if not shortages:
            return

        detail = "\n".join(
            _("• %(product)s — solicitado: %(needed)s, disponible: %(available)s %(uom)s",
              product=product.display_name,
              needed=qty_needed,
              available=available,
              uom=product.uom_id.name)
            for product, qty_needed, available in shortages
        )
        raise UserError(_(
            "No hay stock suficiente para apartar esta orden en el almacén "
            "%(warehouse)s.\n\n%(detail)s\n\n"
            "Reabastezca los productos o ajuste las cantidades antes de apartar.",
            warehouse=warehouse.display_name or _('predeterminado'),
            detail=detail,
        ))

    def action_wholesale_reserve(self):
        """Confirma la orden y la deja apartada. Réplica opcional del Confirmar nativo.

        Ejecuta la confirmación estándar de Odoo -generando los movimientos de
        almacén y reservando el stock- y marca el apartado. El estado real sigue
        siendo 'sale', de modo que almacén, conectores y facturación operan con
        normalidad.
        """
        self.ensure_one()
        if not self.data_is_wholesale_sale:
            raise UserError(_(
                "El apartado solo aplica a las ventas al mayoreo. "
                "Utilice el botón Confirmar."
            ))

        # Se valida ANTES de confirmar: si falta stock, la orden no se toca.
        self._wb_check_stock_availability()

        res = self.action_confirm()

        if self.state == 'sale':
            self.write({'data_is_reserved': True})
            self.message_post(body=_(
                "La orden fue confirmada y el stock quedó apartado. "
                "Pasará a %(state)s cuando Finanzas registre el cobro.",
                state=dict(self._wb_sale_state_selection()).get('sale', 'Orden de venta'),
            ))
        return res

    def action_wholesale_confirm(self):
        """Espejo del botón Confirmar nativo con validación de existencias.

        Confirma la orden sin apartarla, pero exigiendo el mismo stock libre que
        el botón Apartar. Así ninguna de las dos vías del flujo de mayoreo puede
        comprometer mercancía que no existe.

        El Confirmar nativo queda oculto en las órdenes de mayoreo; el resto de
        los pedidos lo conservan intacto.
        """
        self.ensure_one()
        if not self.data_is_wholesale_sale:
            raise UserError(_(
                "Esta acción solo aplica a las ventas al mayoreo. "
                "Utilice el botón Confirmar."
            ))

        # Misma validación que el apartado: si falta stock, la orden no se toca.
        self._wb_check_stock_availability()

        return self.action_confirm()

    def action_release_reservation(self):
        """Libera el apartado: la orden pasa a comportarse como una venta normal."""
        self.filtered('data_is_reserved').write({'data_is_reserved': False})

    @api.constrains('data_is_wholesale_sale', 'data_carrier_selection_relational')
    def _check_wholesale_carrier_required(self):
        """La paquetería es obligatoria en las ventas al mayoreo.

        La vista ya marca el campo como requerido; esta validación cubre las
        escrituras por código, importaciones y API.
        """
        for order in self:
            if order.data_is_wholesale_sale and not order.data_carrier_selection_relational:
                raise ValidationError(_(
                    "Las ventas al mayoreo requieren una paquetería o carrier. "
                    "Selecciónelo en la pestaña 'Datos necesarios' de la orden %(order)s.",
                    order=order.name or _('nueva'),
                ))

    # --------------------------------------------------------------------------------
    # Candado de facturación mientras la orden está apartada
    def _create_invoices(self, grouped=False, final=False, date=None):
        reserved_orders = self.filtered('data_is_reserved')
        if reserved_orders:
            raise UserError(_(
                "No se puede facturar una orden apartada. Finanzas debe registrar "
                "el cobro para liberar el apartado.\n\nÓrdenes afectadas: %(orders)s",
                orders=", ".join(reserved_orders.mapped('name')),
            ))
        return super()._create_invoices(grouped=grouped, final=final, date=date)

    # ================================================================================
    # ACTIVIDADES Y SEGUIDORES
    # ================================================================================
    # La orden arranca con los seguidores nativos de Odoo: el cliente y el vendedor.
    # Cada responsable se suma como seguidor ÚNICAMENTE cuando se le asigna su
    # actividad, de modo que la lista queda acotada a los involucrados reales en
    # lugar de crecer con todos los usuarios de los grupos de mayoreo.

    data_ready_pick_activity_done = fields.Boolean(
        string='Actividad de surtido generada',
        default=False,
        copy=False,
        readonly=True,
        help='Técnico: evita que la actividad "Pedido listo para surtir" se genere '
             'más de una vez por orden.'
    )

    data_carrier_is_internal = fields.Boolean(
        string='Carrier de logística interna',
        related='data_carrier_selection_relational.data_is_internal_logistics',
        readonly=True,
        help='Refleja la casilla del carrier seleccionado. Determina a qué responsable '
             'se asigna la actividad de guía.'
    )

    def _wb_activity_user(self, config_field):
        """Responsable configurado para una actividad, con respaldo en el vendedor.

        Si el ajuste está vacío o apunta a un usuario archivado, la actividad se
        asigna al vendedor de la orden (o al usuario actual si no hay vendedor) y
        se deja constancia en el log para que el manager de mayoreo complete la
        configuración en Ventas → Configuración → Venta mayoreo.
        """
        self.ensure_one()
        config = self.env['wb.wholesale.config']._get_config(self.company_id)
        user = config[config_field]
        if user and user.active:
            return user

        fallback = self.user_id or self.env.user
        _logger.warning(
            "wb_sale_wholesale_approval: el responsable '%s' no está configurado para la "
            "compañía %s (Ventas → Configuración → Venta mayoreo → Configuración de "
            "responsables). La actividad de la orden %s se asignó a %s.",
            config_field, self.company_id.display_name, self.name, fallback.display_name,
        )
        return fallback

    def _wb_schedule_activity(self, summary, note, user, date_deadline=None):
        """Agenda una actividad y suma a su responsable como seguidor de la orden."""
        self.ensure_one()
        self.activity_schedule(
            'mail.mail_activity_data_todo',
            summary=summary,
            note=note,
            user_id=user.id,
            date_deadline=date_deadline or fields.Date.context_today(self),
        )
        if user.partner_id:
            self.message_subscribe(partner_ids=user.partner_id.ids)

    def _wb_check_ready_to_pick(self):
        """Genera la actividad de surtido cuando la orden queda lista para recolectar.

        Se apoya en data_ready_to_pick (WB_data_sale_order), que es verdadero cuando
        la orden tiene número de guía y al menos un documento adjunto.
        """
        for order in self:
            if not order.data_is_wholesale_sale or order.data_ready_pick_activity_done:
                continue
            if not order.data_ready_to_pick or order.state != 'sale':
                continue

            picking_user = order._wb_activity_user('data_wholesale_picking_user_id')
            order._wb_schedule_activity(
                _('Pedido listo para surtir'),
                _('La orden ya cuenta con guía y documentación: puede programarse su surtido.'),
                picking_user,
            )
            order.data_ready_pick_activity_done = True

    # --------------------------------------------------------------------------------
    # Métodos para los botones de cambio de estado
    def action_set_to_receipt_received(self):
        self.ensure_one()
        # Solo se puede pasar a 'comprobante recibido' desde 'pendiente'
        if self.data_finance_approval_status in ['pending']:

            # La fecha de entrega es obligatoria para pasar a 'En validación':
            # Finanzas la necesita para comprometer el surtido.
            if not self.commitment_date:
                raise UserError(_(
                    "Falta capturar la Fecha de entrega.\n\n"
                    "Debe llenarla antes de pasar la orden a 'En validación'."
                ))

            # -------- Buscar y marcar la actividad de comprobante de pago como hecha -----------------------------
            # Referencia al tipo de actividad
            activity_type_id = self.env.ref('mail.mail_activity_data_todo').id
            # Referencia al modelo de la orden de venta
            sale_order_model_id = self.env.ref('sale.model_sale_order').id

            # Buscar la actividad con el dominio
            activities_to_lock = self.env['mail.activity'].search([
                ('res_id', '=', self.id),
                ('res_model_id', '=', sale_order_model_id),
                ('activity_type_id', '=', activity_type_id),
                ('summary', '=', 'Pendiente de comprobante de pago')
            ])

            # Marcar las actividades encontradas como hechas
            if activities_to_lock:
                activities_to_lock.action_done()

            # ----------------------------------------------------------------------
            # Creacion de actividad para verificacion del pago.
            # El responsable de Finanzas se suma como seguidor en este momento.
            self.data_confirmation_date = datetime.now()

            self._wb_schedule_activity(
                _('Revisión de aprobación financiera'),
                _('Revisar comprobante de pago y actualizar el estado financiero de esta orden de venta al mayoreo.'),
                self._wb_activity_user('data_wholesale_finance_user_id'),
            )

            # ----------------------------------------------------------------------
            # Colocar estado en validacion
            self.write({'data_finance_approval_status': 'validation'})
            self.partner_id._refresh_wholesale_credit_available()


    def action_set_to_collected(self):
        self.ensure_one()
        if self.data_finance_approval_status in ['validation']:
        # --------------------------------------------------------------------------------
            carrier = self.data_carrier_selection_relational

            vals = {'data_finance_approval_status': 'collected'}

            # El autollenado de la guía en Pick Up se conserva tal cual estaba.
            # Sigue dependiendo del nombre del carrier: pasarlo a configuración
            # corresponde a la Etapa 2 del plan de deuda técnica.
            if carrier and carrier.name == 'Pick Up':
                vals['yuju_carrier_tracking_ref'] = 'Pick-up'

            self.write(vals)

            # Actividad de guía. Se enruta según el tipo de carrier y su
            # responsable se suma como seguidor en este momento.
            if carrier and carrier.data_is_internal_logistics:
                responsible_field = 'data_wholesale_internal_user_id'
                note = _('Generar la guía o etiqueta interna para esta orden '
                         '(logística interna / Pick Up).')
            else:
                responsible_field = 'data_wholesale_carrier_user_id'
                note = _('Favor de generar la guía de paquetería para esta orden.')

            activity_user = self._wb_activity_user(responsible_field)
            _logger.info(
                "wb_sale_wholesale_approval: orden %s, carrier '%s' (logística interna: %s) "
                "-> actividad de guía asignada a %s por el ajuste '%s'.",
                self.name, carrier.name if carrier else '-',
                bool(carrier and carrier.data_is_internal_logistics),
                activity_user.display_name, responsible_field,
            )

            self._wb_schedule_activity(
                _('Selección de carrier y generación de guía'),
                note,
                activity_user,
            )

            # -----------------------------------------------------------------------
            # -------- Buscar y marcar la actividad de validacion de pago como hecha -----------------------------

            # Referencia al tipo de actividad
            activity_type_id = self.env.ref('mail.mail_activity_data_todo').id
            # Referencia al modelo de la orden de venta
            sale_order_model_id = self.env.ref('sale.model_sale_order').id

            # Buscar la actividad con el dominio
            activities_to_lock = self.env['mail.activity'].search([
                ('res_id', '=', self.id),
                ('res_model_id', '=', sale_order_model_id),
                ('activity_type_id', '=', activity_type_id),
                ('summary', '=', 'Revisión de aprobación financiera')
            ])

            # Marcar las actividades encontradas como hechas
            if activities_to_lock:
                activities_to_lock.action_done()

            # Con el cobro registrado se libera el apartado: la barra de estado
            # pasa a Orden de venta y se habilita la facturación.
            self.action_release_reservation()
            self.partner_id._refresh_wholesale_credit_available()

    def action_set_to_rejected(self):
        self.ensure_one()
        if self.data_finance_approval_status in ['validation']:

            # Cerrar actividades pendientes relacionadas con esta orden
            activities_to_lock = self.env['mail.activity'].search([
                ('res_id', '=', self.id),
                ('res_model', '=', 'sale.order'),
            ])
            if activities_to_lock:
                activities_to_lock.action_done()

            # Validar que no haya fecha efectiva
            if not self.effective_date:
                # Cancelar la orden de venta FORZADAMENTE
                self.action_unlock()
                self._action_cancel()
                self.message_post(
                    body=_("La orden de venta ha sido cancelada debido al rechazo del pago.")
                )
            else:
                # Si no cumple condiciones, solo dejar el estado financiero en 'rejected'
                self.message_post(
                    body=_(
                        "El pago ha sido rechazado, pero la orden no fue cancelada porque ya tiene fecha efectiva")
                )

            self.write({'data_finance_approval_status': 'rejected'})
            self.partner_id._refresh_wholesale_credit_available()

    # -------------------------------------------------------------------------------------------
    # Sobreescribir el método de confirmación
    def _wb_freeze_partner_credit_snapshot(self):
        """Congela en la orden los datos de crédito del cliente al confirmar.

        Sustituye al antiguo compute almacenado, que se recalculaba y reescribía
        en todas las órdenes de la base ante cualquier cambio del cliente o del
        estado. Aquí se escribe una sola vez, solo en las órdenes de mayoreo y
        solo en el momento de la confirmación.
        """
        for order in self.filtered('data_is_wholesale_sale'):
            partner = order.partner_id
            order.write({
                'data_partner_credit_approved': partner.data_credit_approved,
                'data_partner_credit_limit_amount': partner.data_credit_limit or 0.0,
                'data_partner_credit_available_amount': partner.data_credit_available or 0.0,
            })

    def action_confirm(self):
        # El snapshot se congela ANTES de confirmar: a partir de aquí la orden
        # deja de reflejar los valores vivos del cliente.
        self._wb_freeze_partner_credit_snapshot()

        res = super(SaleOrder, self).action_confirm()

        # La orden confirmada pasa a contar como deuda en tránsito del cliente.
        self.mapped('partner_id')._refresh_wholesale_credit_available()

        if self.data_is_wholesale_sale:
            self.data_confirmation_date = datetime.now()
            self.data_finance_approval_status = 'pending'

            # ---------------------------------------------------------
            if self.data_is_credit_sale and self.data_debit_amount == 0:
                # Pago 100% a crédito, saltar a VALIDATION.
                # El responsable de Finanzas se suma como seguidor aquí.
                self.data_finance_approval_status = 'validation'

                self._wb_schedule_activity(
                    _('Revisión de aprobación financiera'),
                    _('Pago 100% con crédito: revisar y validar el crédito disponible del cliente.'),
                    self._wb_activity_user('data_wholesale_finance_user_id'),
                )
            else:
                # Caso normal, va a PENDING comprobante.
                # Se asigna al vendedor, que ya es seguidor nativo de la orden: en
                # esta etapa la lista se mantiene en cliente + vendedor.
                self.data_finance_approval_status = 'pending'

                self.activity_schedule(
                    'mail.mail_activity_data_todo',
                    summary=_('Pendiente de comprobante de pago'),
                    note=_('Dar seguimiento al envío del comprobante de pago correspondiente.'),
                    user_id=(self.user_id or self.env.user).id,
                    date_deadline=datetime.now() + timedelta(hours=72),
                )

        return res

    # -------------------------------------------------------------------------------------------
    # Sobreescribir el método de cancelar
    def action_cancel(self):
        for order in self:
            if order.data_is_wholesale_sale:
                #Cerrar actividades pendientes
                activities_to_lock = self.env['mail.activity'].search([
                    ('res_id', '=', order.id),
                    ('res_model', '=', 'sale.order'),
                ])

                if activities_to_lock:
                    activities_to_lock.action_done()

                #Limpiar estado financiero
                order.data_finance_approval_status = False
                order.data_is_reserved = False

        res = super(SaleOrder, self).action_cancel()

        # Una orden cancelada deja de pesar en la deuda en tránsito.
        self.mapped('partner_id')._refresh_wholesale_credit_available()
        return res


    # -------------------------------------------------------------------------------------------
    # Sobreescribir el método de escritura
    def write(self, vals):
        # Verificacion de equipo de ventas 'Team_Mayoreo' para edicion con Write
        if vals.get('data_is_wholesale_sale'):
            team_mayoreo = self.env['crm.team'].search([('name', '=', 'Team_Mayoreo')], limit=1)
            if team_mayoreo:
                vals['team_id'] = team_mayoreo.id

        # Nota: se itera con filtered() en lugar de evaluar self.data_is_wholesale_sale,
        # que lanzaba ValueError (singleton) en cualquier escritura de varios registros.
        if 'yuju_carrier_tracking_ref' in vals and vals['yuju_carrier_tracking_ref']:
            for order in self.filtered('data_is_wholesale_sale'):
                carrier_activities = self.env['mail.activity'].search([
                    ('res_id', '=', order.id),
                    ('res_model', '=', 'sale.order'),
                    ('summary', '=', 'Selección de carrier y generación de guía')
                ])
                if carrier_activities:
                    carrier_activities.action_done()

        res = super().write(vals)

        # data_ready_to_pick (WB_data_sale_order) es un calculado almacenado que
        # depende de la guía y de los adjuntos: al escribir la guía se revisa si la
        # orden ya quedó lista para surtir.
        if 'yuju_carrier_tracking_ref' in vals:
            self._wb_check_ready_to_pick()

        return res

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

        limit_date = fields.Datetime.now() - timedelta(hours=144)

        domain = [
            ('data_is_wholesale_sale', '=', True),  
            ('data_finance_approval_status', '=', 'pending'),  
            ('state', '=', 'sale'), # En Odoo 18 solo se evalua 'sale'
            ('data_confirmation_date', '<', limit_date) # Odoo ya acepta el objeto Datetime directo, no necesitas strftime
        ]
        
        old_orders = self.env['sale.order'].search(domain)
        _logger.info(f"Se encontraron {len(old_orders)} órdenes antiguas que serán canceladas.")

        partners_to_refresh = old_orders.mapped('partner_id')

        # Cancelación en lote: _action_cancel, la limpieza de campos y el cierre
        # de actividades operan sobre todo el conjunto en lugar de repetirse por
        # orden. El resultado es el mismo; lo que cambia es el número de
        # consultas, que pasa de crecer con las órdenes a ser constante.
        old_orders._action_cancel()

        # Limpiar los campos manualmente, porque _action_cancel se salta action_cancel
        wholesale_orders = old_orders.filtered('data_is_wholesale_sale')
        if wholesale_orders:
            wholesale_orders.write({
                'data_finance_approval_status': False,
                'data_is_reserved': False,
            })

            activities_to_lock = self.env['mail.activity'].search([
                ('res_model', '=', 'sale.order'),
                ('res_id', 'in', wholesale_orders.ids),
            ])
            if activities_to_lock:
                activities_to_lock.action_done()

        for order in old_orders:
            order.message_post(
                body="La orden de venta ha sido cancelada automáticamente por superar el plazo de 6 días sin confirmación de pago.")
            _logger.info(f"La orden de venta {order.name} ha sido cancelada.")

        partners_to_refresh._refresh_wholesale_credit_available()

    # ----------------------------------------------------------------------------------
    # Lógica para el aviso en el chatter de órdenes pendientes
    @api.model
    def _cron_send_payment_reminder_message(self):
        _logger.info("El cron de aviso 'pago pendiente ventas mayoreo' se está ejecutando.")

        activity_type_id = self.env.ref('mail.mail_activity_data_todo').id
        
        today = fields.Date.today()

        overdue_activities = self.env['mail.activity'].search([
            ('res_model_id', '=', self.env.ref('sale.model_sale_order').id),
            ('activity_type_id', '=', activity_type_id),
            ('summary', '=', 'Pendiente de comprobante de pago'), #evitar spam falso!
            ('date_deadline', '<', today)
        ])

        if not overdue_activities:
            _logger.info("No hay actividades de cobro vencidas. Fin del cron.")
            return

        orders_to_remind = self.env['sale.order'].search([
            ('id', 'in', overdue_activities.mapped('res_id')),
            ('state', '=', 'sale'),
            ('data_is_wholesale_sale', '=', True),
            ('data_finance_approval_status', '=', 'pending'),
        ])

        _logger.info(f"Se encontraron {len(orders_to_remind)} órdenes de venta que necesitan un aviso de pago.")

        for order in orders_to_remind:
            message_body = "El pago de esta orden de venta al mayoreo está vencido. Por favor, revísalo y actualiza el estado financiero."

            # Obtiene el ID del vendedor asignado a la orden
            if order.user_id:
                order.message_post(
                    body=message_body,
                    message_type='comment',
                    subtype_xmlid='mail.mt_comment',
                    author_id=order.user_id.partner_id.id
                )
                _logger.info(f"Se envió un aviso para la orden {order.name}, remitente: {order.user_id.name}.")
            else:
                _logger.warning(f"No se encontró un vendedor asignado para la orden {order.name}. No se pudo enviar el aviso.")

        _logger.info("El cron de aviso 'pago pendiente ventas mayoreo' ha finalizado.")


class SaleOrderAttachment(models.Model):
    """Los adjuntos alimentan data_ready_to_pick (WB_data_sale_order).

    Al cargar uno, se revisa si la orden ya quedó lista para surtir, que es el
    momento en que se genera la actividad de surtido.
    """
    _inherit = 'sale.order.attachment'

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        records.mapped('so_id')._wb_check_ready_to_pick()
        return records
