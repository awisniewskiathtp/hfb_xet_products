# -*- coding: utf-8 -*-
# vim: tabstop=4 softtabstop=0 shiftwidth=4 smarttab expandtab fileformat=unix
#################################################################################
#
# Odoo, Open ERP Source Management Solution
# Copyright (C) 2017-2026 Hadron for Business sp. z o.o. (http://hadronforbusiness.com)
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as
# published by the Free Software Foundation, either version 3 of the
# License, or (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.
#
#################################################################################
# UWAGA / NOTICE:
# "XET" oraz nazwa "Hadron for Business" są zastrzeżonymi znakami towarowymi
# "XET" and "Hadron for Business" are trademarks of Hadron for Business sp. z o.o.
#
# Sam kod jest objęty licencją AGPLv3, ale koncepcje, pomysły i rozwiązania
# biznesowe w nim zawarte nie są objęte tą licencją i pozostają własnością
# autora.
# The code is licensed under AGPLv3, but the concepts, ideas and business
# solutions contained herein are not covered by this license and remain the
# property of the author.
#################################################################################
"""	@version 20.0.0.3.0
	@owner  Hadron for Business Sp. z o.o.
	@author Andrzej Wiśniewski (warp3r)
	@date   2026-11-17

	Migracja do 20.0.0.3.0 (dane szablonów mają noupdate):
	  - szablon eksportu xet_template_products: przeznaczenie 'export',
		model pętli (loop_model_id) dla węzłów Product i img - bez niego
		eksport JSON nie przenosił pól relacji pętli,
	  - kanały importu bez szablonu: domyślny szablon importu.
"""
import logging

from odoo import SUPERUSER_ID, api

_logger = logging.getLogger(__name__)


def migrate(cr, version):
	env = api.Environment(cr, SUPERUSER_ID, {})
	export_tpl = env.ref("hfb_xet_products.xet_template_products", raise_if_not_found=False)
	if export_tpl:
		if not export_tpl.xp_usage:
			export_tpl.xp_usage = "export"
		for node in export_tpl.node_ids.filtered(lambda n: n.loop_rel_field_id and not n.loop_model_id):
			node.loop_model_id = node.loop_rel_field_id.model_id
			_logger.info("[XP] migracja: loop_model_id węzła %s = %s", node.xpath, node.loop_rel_field_id.model)
	import_tpl = env.ref("hfb_xet_products.xet_template_products_import", raise_if_not_found=False)
	if import_tpl:
		configs = env["communication.provider.xet_products"].with_context(active_test=False).search([
			("direction", "=", "import"), ("template_id", "=", False),
		])
		configs.write({"template_id": import_tpl.id})
		_logger.info("[XP] migracja: szablon importu przypisany do kanałów %s", configs.ids)
