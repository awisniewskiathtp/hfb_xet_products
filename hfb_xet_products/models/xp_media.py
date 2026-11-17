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


	Media produktu (linki).

	Media są przechowywane jako linki (bez plików). Typ medium jest rekordem
	słownika `xp.media.type` - nowy rodzaj linku to nowy rekord słownika,
	bez zmian w kodzie i strukturze tabel.
"""
from odoo import fields, models


class XpMediaType(models.Model):
	_name = "xp.media.type"
	_description = "XET Products - typ medium"
	_order = "sequence, id"

	name = fields.Char(string="Nazwa", required=True, translate=True)
	code = fields.Char(string="Kod", required=True, index=True)
	sequence = fields.Integer(default=10)
	active = fields.Boolean(default=True)

	_code_unique = models.Constraint(
		"UNIQUE(code)",
		"Kod typu medium musi być unikalny.",
	)


class XpProductMedia(models.Model):
	_name = "xp.product.media"
	_description = "XET Products - medium produktu (link)"
	_order = "product_tmpl_id, media_type_id, sequence, id"

	product_tmpl_id = fields.Many2one(
		"product.template", string="Produkt", required=True,
		index=True, ondelete="cascade",
	)
	media_type_id = fields.Many2one(
		"xp.media.type", string="Typ", required=True, index=True,
		ondelete="restrict",
	)
	media_type_code = fields.Char(related="media_type_id.code", store=True, index=True)
	url = fields.Char(string="URL", required=True)
	size = fields.Char(string="Rozmiar")
	sequence = fields.Integer(default=10)
#EoF

