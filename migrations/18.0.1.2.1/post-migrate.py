# -*- coding: utf-8 -*-
"""Limpieza de las etiquetas de selección obsoletas de data_sale_state.

La primera versión del campo declaraba la selección como una lista estática
('Cotización', 'Cotización enviada', 'Apartado', 'Venta', 'Cancelado'), y Odoo
la reflejó en ir_model_fields_selection.

Ahora la selección se resuelve en tiempo de ejecución desde el campo nativo
sale.order.state, para que los rótulos coincidan exactamente con los de Odoo
('Orden de venta' en lugar de 'Venta') en cualquier idioma.

_reflect_selections() solo procesa campos cuya selección es una lista
(odoo/addons/base/models/ir_model.py:1549), por lo que nunca borra las filas
que dejó la definición anterior. Se eliminan aquí.
"""
import logging

_logger = logging.getLogger(__name__)

MODEL = 'sale.order'
FIELD = 'data_sale_state'


def migrate(cr, version):
    if not version:
        return

    # ir.model.data asociados a esas filas de selección
    cr.execute("""
        DELETE FROM ir_model_data
        WHERE model = 'ir.model.fields.selection'
          AND res_id IN (
              SELECT s.id
              FROM ir_model_fields_selection s
              JOIN ir_model_fields f ON f.id = s.field_id
              WHERE f.model = %s AND f.name = %s
          )
    """, (MODEL, FIELD))

    cr.execute("""
        DELETE FROM ir_model_fields_selection s
        USING ir_model_fields f
        WHERE s.field_id = f.id
          AND f.model = %s
          AND f.name = %s
    """, (MODEL, FIELD))

    _logger.info(
        "wb_sale_wholesale_approval: eliminadas %s etiquetas obsoletas de %s.%s",
        cr.rowcount, MODEL, FIELD,
    )
