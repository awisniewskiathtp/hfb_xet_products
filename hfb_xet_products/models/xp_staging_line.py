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


	xp.staging.line - linie przebiegu importu (etap parse -> apply).

	Jedna linia = jeden <Product> z pliku. Linie pozwalają:
	  - skontrolować dane przed zapisem do produktów,
	  - wznowić etap apply po przerwaniu (linie 'draft' czekają),
	  - przejrzeć błędy po przebiegu.
	Linie są usuwane razem z przebiegiem (ondelete=cascade).
"""
from odoo import fields, models


class XpStagingLine(models.Model):
	_name = "xp.staging.line"
	_description = "XET Products - linia importu"
	_order = "log_id, sequence, id"

	log_id = fields.Many2one(
		"communication.log", string="Przebieg", required=True,
		index=True, ondelete="cascade",
	)
	sequence = fields.Integer(string="Pozycja w pliku", index=True)
	state = fields.Selection(
		[
			("draft", "Wczytana"),
			("processed", "Przetworzona"),
			("error", "Błąd"),
		],
		default="draft", required=True, index=True,
	)
	action = fields.Selection(
		[("created", "Utworzono"), ("updated", "Zaktualizowano"), ("unchanged", "Bez zmian")],
		string="Wynik",
	)
	sku = fields.Char(string="SKU", index=True)
	ean = fields.Char(string="EAN", index=True)
	payload = fields.Json(string="Dane")
	error_message = fields.Text(string="Błąd")
	product_tmpl_id = fields.Many2one("product.template", string="Produkt", ondelete="set null")
	image_state = fields.Selection(
		[
			("done", "Pobrano"),
			("skipped", "Bez zmian"),
			("error", "Błąd pobrania"),
		],
		string="Zdjęcie główne", index=True,
		help="Wynik etapu images. Puste = jeszcze nie przetworzono.",
	)
#EoF
