# -*- coding: utf-8 -*-
"""Traslada los responsables de res.company al modelo wb.wholesale.config.

Hasta la versión 18.0.1.3.0 los tres responsables vivían como campos de
res.company y se editaban desde Ajustes de Ventas, lo que exigía permisos de
administrador del sistema. Ahora viven en wb.wholesale.config, que el manager
de mayoreo mantiene desde su propio menú.

Odoo no elimina las columnas de un campo retirado del código, así que los
valores anteriores siguen en res_company y se copian aquí. Las columnas
huérfanas se conservan por seguridad: no las lee nadie.
"""
import logging

_logger = logging.getLogger(__name__)

FIELDS = (
    'data_wholesale_finance_user_id',
    'data_wholesale_carrier_user_id',
    'data_wholesale_picking_user_id',
)


def migrate(cr, version):
    if not version:
        return

    # Solo las columnas que realmente existan en res_company
    cr.execute("""
        SELECT column_name
        FROM information_schema.columns
        WHERE table_name = 'res_company' AND column_name IN %s
    """, (FIELDS,))
    present = [row[0] for row in cr.fetchall()]
    if not present:
        _logger.info("wb_sale_wholesale_approval: no hay responsables previos que migrar.")
        return

    cr.execute(
        "SELECT id, %s FROM res_company" % ", ".join(present)
    )
    rows = cr.fetchall()

    migrated = 0
    for row in rows:
        company_id, values = row[0], dict(zip(present, row[1:]))
        if not any(values.values()):
            continue

        cr.execute(
            "SELECT id FROM wb_wholesale_config WHERE company_id = %s", (company_id,)
        )
        existing = cr.fetchone()

        columns = list(values.keys())
        if existing:
            assignments = ", ".join("%s = %%s" % col for col in columns)
            cr.execute(
                "UPDATE wb_wholesale_config SET %s WHERE id = %%s" % assignments,
                [values[col] for col in columns] + [existing[0]],
            )
        else:
            cr.execute(
                "INSERT INTO wb_wholesale_config (company_id, %s) VALUES (%s)"
                % (", ".join(columns), ", ".join(["%s"] * (len(columns) + 1))),
                [company_id] + [values[col] for col in columns],
            )
        migrated += 1

    _logger.info(
        "wb_sale_wholesale_approval: responsables migrados a wb.wholesale.config "
        "para %s compañía(s).", migrated,
    )
