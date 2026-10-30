# -*- coding: utf-8 -*-
from odoo import models, fields, api, _
from odoo.exceptions import ValidationError
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
        help="Límite de crédito menos la parte aún no cubierta de las ventas a crédito. Las ventas de contado no consumen crédito."
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
        # El grupo del usuario no depende del contacto: se evalúa una sola vez
        # y no en cada iteración.
        is_finance_user = self.env.user.has_group('wb_sale_wholesale_approval.group_finance_user')
        for partner in self:
            partner.can_edit_credit_limit = partner.data_credit_approved and is_finance_user

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
    # Las dependencias NO incluyen sale_order_ids.*: esa relación inversa obligaba
    # a Odoo a recalcular el crédito de los clientes en cada escritura sobre
    # cualquier orden de venta (estado, invoice_status, montos), resolviendo un
    # one2many masivo dentro del write path. Era el origen del problema de
    # rendimiento y del consumo de memoria en procesos por lotes, como la
    # validación de transferencias de almacén.
    # A cambio, el refresco puntual se hace con _refresh_wholesale_credit_available()
    # en los momentos del flujo de mayoreo que sí alteran el crédito.
    @api.depends(
        'data_credit_limit',
        'credit',
        'company_id.data_use_automated_credit'
    )
    def _compute_credit_available(self):
        for partner in self:
            partner.data_credit_available = partner._wb_credit_available_excluding()

    def _wb_credit_available_excluding(self, orders=None):
        """Crédito disponible del cliente.

        El parámetro `orders` permite preguntar "¿cuánto crédito tendría este
        cliente si estas órdenes no existieran?", que es lo que necesita la
        validación al editar el monto de una orden ya confirmada: su importe
        actual ya está comprometido y, sin excluirlo, se compararía contra sí
        mismo.
        """
        self.ensure_one()

        if not self.data_credit_approved or self.data_credit_limit <= 0:
            return 0.0

        if not self.env.company.data_use_automated_credit:
            # MODO MANUAL: el disponible es el límite, sin descuentos.
            return self.data_credit_limit

        consumed = self._wb_credit_consumed(exclude_orders=orders)
        return max(0.0, self.data_credit_limit - consumed)

    def _wb_credit_consumed(self, exclude_orders=None):
        """Crédito comprometido: la parte del monto a crédito aún no cubierta.

        Se calcula ÍNTEGRAMENTE desde las órdenes marcadas como venta a crédito.
        Deliberadamente NO se usa partner.credit: ese campo es la cartera
        completa del cliente e incluye las facturas de ventas de contado, que
        por regla de negocio no deben consumir límite de crédito.

        Por cada orden:

            comprometido = monto_a_crédito × (1 − fracción_cobrada)
            fracción_cobrada = fracción_facturada × (1 − fracción_sin_pagar)

        de modo que el crédito se recupera de forma proporcional conforme el
        cliente abona, y no de golpe al liquidar la factura.
        """
        self.ensure_one()

        real_partner_id = self.commercial_partner_id.id or self.id
        if not real_partner_id:
            return 0.0

        # Solo entran las órdenes que todavía pueden deber algo: las que tienen
        # algo por facturar, o las que ya se facturaron pero cuya factura no está
        # saldada. Las liquidadas quedan fuera, de modo que el conjunto no crece
        # con el histórico.
        domain = [
            ('order_id.partner_id', 'child_of', real_partner_id),
            ('order_id.state', '=', 'sale'),
            ('order_id.data_is_credit_sale', '=', True),
            '|',
                ('order_id.invoice_status', 'in', ['to invoice', 'no']),
                '&',
                    ('order_id.order_line.invoice_lines.move_id.state', '!=', 'cancel'),
                    ('order_id.order_line.invoice_lines.move_id.payment_state', 'not in',
                     ['paid', 'reversed']),
        ]
        if exclude_orders:
            domain.append(('order_id', 'not in', exclude_orders.ids))

        # Agregación en SQL: price_subtotal y untaxed_amount_invoiced están
        # almacenados, así que no se materializan las líneas.
        line_rows = self.env['sale.order.line']._read_group(
            domain,
            ['order_id'],
            ['price_subtotal:sum', 'untaxed_amount_invoiced:sum'],
        )
        if not line_rows:
            return 0.0

        # Precarga en lote. Los recordsets que devuelve _read_group son de un
        # solo registro cada uno, así que leerlos dentro del bucle dispararía
        # una consulta por orden -y otra por cada juego de facturas-. Agrupando
        # los ids en un único recordset, el ORM resuelve todo en unas pocas
        # consultas, sin importar cuántas órdenes haya.
        totals_by_order = {
            order.id: (total_untaxed, invoiced_untaxed)
            for order, total_untaxed, invoiced_untaxed in line_rows
        }
        orders = self.env['sale.order'].browse(list(totals_by_order))
        orders.fetch(['data_credit_amount'])

        invoices = orders.invoice_ids
        if invoices:
            invoices.fetch(['state', 'move_type', 'amount_total', 'amount_residual'])

        consumed = 0.0
        for order in orders:
            total_untaxed, invoiced_untaxed = totals_by_order[order.id]

            credit_amount = order.data_credit_amount or 0.0
            if not credit_amount:
                continue

            # Fracción de la orden ya facturada. untaxed_amount_invoiced solo
            # cuenta facturas PUBLICADAS: con una factura en borrador la orden
            # sigue comprometiendo el crédito completo, que es lo correcto.
            if total_untaxed > 0:
                invoiced_ratio = min(1.0, max(0.0, (invoiced_untaxed or 0.0) / total_untaxed))
            else:
                invoiced_ratio = 0.0

            # De lo facturado, qué proporción sigue sin pagarse.
            unpaid_ratio = order._wb_invoiced_unpaid_ratio()

            paid_ratio = invoiced_ratio * (1.0 - unpaid_ratio)
            consumed += credit_amount * (1.0 - paid_ratio)

        return consumed

    def _refresh_wholesale_credit_available(self):
        """Invalida el crédito disponible tras un cambio del flujo de mayoreo.

        Como el compute ya no depende de sale_order_ids, Odoo no se entera de que
        una orden modificó la deuda en tránsito. Este método marca el valor en
        caché como obsoleto para que se recalcule la próxima vez que alguien lo
        lea.

        Se usa invalidate_recordset en lugar de invocar el compute directamente:
        invalidar no ejecuta ninguna consulta, mientras que llamar al compute
        lanzaría una agregación por cada cliente aunque nadie fuera a leer el
        campo -coste que se pagaba en cada confirmación por lotes-.
        """
        if not self:
            return self
        self.invalidate_recordset(['data_credit_available'])
        return self

    # ====================================================================
    # DATOS DE CONTACTO OBLIGATORIOS AL CREAR UN CONTACTO
    # ====================================================================
    # Campos exigidos cuando el ajuste de la compañía está activo.
    # Clave del campo -> etiqueta mostrada al usuario.
    WB_REQUIRED_CONTACT_FIELDS = {
        'phone': 'Teléfono',
        'email': 'Correo electrónico',
        'country_id': 'País',
        'zip': 'Código postal',
    }

    data_contact_data_required = fields.Boolean(
        string='Datos de contacto obligatorios',
        compute='_compute_data_contact_data_required',
        help='Técnico: refleja el ajuste de la compañía para marcar los campos como obligatorios en la vista.'
    )

    @api.depends_context('company')
    def _compute_data_contact_data_required(self):
        required = self.env.company.data_require_partner_contact_data
        for partner in self:
            partner.data_contact_data_required = required

    def _wb_check_required_contact_data(self):
        """Valida los datos de contacto de los contactos raíz (sin contacto padre).

        Se omite durante la instalación/carga de datos y cuando se pasa el contexto
        'skip_wb_partner_contact_check' (procesos automáticos, migraciones, etc.).
        """
        if self.env.context.get('install_mode') or self.env.context.get('skip_wb_partner_contact_check'):
            return
        if not self.env.company.data_require_partner_contact_data:
            return

        for partner in self:
            # Solo contactos raíz: empresas e individuos sin contacto relacionado.
            if partner.parent_id:
                continue

            missing = [
                label for fname, label in self.WB_REQUIRED_CONTACT_FIELDS.items()
                if not partner[fname]
            ]
            if missing:
                raise ValidationError(_(
                    "No se puede crear el contacto «%(name)s»: faltan datos obligatorios.\n\n%(fields)s\n\n"
                    "Un administrador puede desactivar esta validación en "
                    "Ventas → Configuración → Ventas al Mayoreo.",
                    name=partner.display_name or _('Nuevo'),
                    fields="\n".join("• %s" % label for label in missing),
                ))

    @api.model_create_multi
    def create(self, vals_list):
        partners = super().create(vals_list)
        partners._wb_check_required_contact_data()
        return partners


class ResUsers(models.Model):
    _inherit = 'res.users'

    @api.model_create_multi
    def create(self, vals_list):
        # La creación de un usuario genera su contacto interno: no debe quedar
        # bloqueada por la validación de datos de contacto comerciales.
        return super(ResUsers, self.with_context(skip_wb_partner_contact_check=True)).create(vals_list)
