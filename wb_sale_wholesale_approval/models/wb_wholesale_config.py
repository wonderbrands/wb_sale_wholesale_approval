# -*- coding: utf-8 -*-
from odoo import models, fields, api, _


class WbWholesaleConfig(models.Model):
    """Configuración operativa de las ventas al mayoreo.

    Vive en su propio modelo -y no en res.company ni en res.config.settings-
    para que el manager de mayoreo pueda mantenerla desde el menú de Ventas sin
    necesidad de permisos de administrador del sistema.
    """
    _name = 'wb.wholesale.config'
    _description = 'Configuración de Ventas al Mayoreo'
    _rec_name = 'company_id'

    company_id = fields.Many2one(
        'res.company',
        string='Compañía',
        required=True,
        ondelete='cascade',
        default=lambda self: self.env.company,
    )

    # ------------------------------------------------------------------
    # Responsables de las actividades del flujo de mayoreo.
    # Cada uno se suma como seguidor de la orden únicamente cuando se le
    # asigna su actividad.
    data_wholesale_finance_user_id = fields.Many2one(
        'res.users',
        string='Responsable de validación de pago',
        help="Usuario al que se asigna la actividad 'Revisión de aprobación financiera'."
    )

    data_wholesale_carrier_user_id = fields.Many2one(
        'res.users',
        string='Responsable de guía por paquetería',
        help="Usuario al que se asigna la actividad de guía cuando el carrier de la "
             "orden es una paquetería externa."
    )

    data_wholesale_internal_user_id = fields.Many2one(
        'res.users',
        string='Responsable de guía por logística interna o Pick Up',
        help="Usuario al que se asigna la actividad de guía cuando el carrier de la "
             "orden está marcado como logística interna o Pick Up."
    )

    data_wholesale_picking_user_id = fields.Many2one(
        'res.users',
        string='Responsable de surtido',
        help="Usuario al que se asigna la actividad 'Pedido listo para surtir' cuando la "
             "orden queda lista para recolectar."
    )

    _sql_constraints = [
        ('company_uniq', 'unique(company_id)',
         'Ya existe una configuración de ventas al mayoreo para esta compañía.'),
    ]

    @api.model
    def _get_config(self, company=None):
        """Configuración de la compañía indicada, creándola la primera vez.

        Se usa sudo porque cualquier usuario que confirme una orden necesita
        leerla, pero solo el manager de mayoreo puede modificarla.
        """
        company = company or self.env.company
        config = self.sudo().search([('company_id', '=', company.id)], limit=1)
        if not config:
            config = self.sudo().create({'company_id': company.id})
        return config

    @api.model
    def action_open_config(self):
        """Abre directamente la configuración de la compañía activa."""
        config = self._get_config()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Configuración de responsables'),
            'res_model': 'wb.wholesale.config',
            'view_mode': 'form',
            'res_id': config.id,
            'target': 'current',
        }
