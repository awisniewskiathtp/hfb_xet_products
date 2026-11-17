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
"""	@version 20.0.0.2.1
	@owner  Hadron for Business Sp. z o.o.
	@author Andrzej Wiśniewski (warp3r)
	@date   2026-11-16


	Rejestracja typu providera `xet_products` w communication.provider (XET Base).

	`_compute_provider_model` w XET Base 20.0 ma słownik typów wpisany na sztywno
	(ksef, localdir) i nie korzysta z `_provider_model_map()`. Bez zmiany XET Base
	nadpisujemy więc tę metodę tutaj, analogicznie jak w hfb_apds.
"""
from odoo import api, fields, models


class CommunicationProvider(models.Model):
	_inherit = "communication.provider"

	provider_type = fields.Selection(
		selection_add=[("xet_products", "XET Products (produkty XML)")],
		ondelete={"xet_products": "set default"},
	)

	@api.model
	def _provider_model_map(self):
		result = super()._provider_model_map()
		result["xet_products"] = "communication.provider.xet_products"
		return result

	@api.depends("provider_type")
	def _compute_provider_model(self):
		model_map = self._provider_model_map()
		for record in self:
			record.provider_model = model_map.get(record.provider_type)
#EoF
