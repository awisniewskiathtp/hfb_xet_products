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


	Pola PIM na product.template (mapowanie zaakceptowane 2026-11-16).

	Klucz produktu (SKU + EAN) jest na product.product (`default_code`,
	`barcode`). Pozostałe pola - na product.template (1 SKU = 1 produkt,
	bez grupowania wariantów).

	Metody `xp_*` są przeznaczone do wywołań z węzłów szablonu XET
	(`value_source = expression`, w kontekście dostępny jest `record`).
"""
from odoo import api, fields, models


class ProductTemplate(models.Model):
	_inherit = "product.template"

	# Identyfikacja
	xp_symbol = fields.Char(string="Symbol")
	xp_name_wms = fields.Char(string="Nazwa WMS")
	xp_link = fields.Char(string="Link (identyfikator)")

	# Producent i marka
	xp_producer = fields.Char(string="Producent")
	xp_producer_data = fields.Text(string="Dane producenta")
	xp_brand = fields.Char(string="Marka")
	xp_safety = fields.Text(string="Bezpieczeństwo", translate=True)
	xp_product_info = fields.Text(string="Informacja producenta", translate=True)

	# Ceny
	xp_price = fields.Float(string="Cena (warunki handlowe)", digits="Product Price")
	xp_price_currency_id = fields.Many2one("res.currency", string="Waluta ceny")

	# Flagi
	xp_news = fields.Boolean(string="Nowość")
	xp_top = fields.Boolean(string="Top")
	xp_hit = fields.Boolean(string="Hit")
	xp_promo = fields.Boolean(string="Promocja")
	xp_outlet = fields.Boolean(string="Outlet")
	xp_hidden_eu = fields.Boolean(string="Ukryty EU")

	# Sortowanie i dostępność (wartości z pliku, nie stany magazynowe Odoo)
	xp_sort_order = fields.Integer(string="Kolejność (strona)")
	xp_sort_order_form = fields.Integer(string="Kolejność (formularz dostępności)")
	xp_available = fields.Boolean(string="Dostępny w magazynie")
	xp_available_qty = fields.Float(string="Ilość w magazynie (z pliku)")
	xp_next_delivery = fields.Char(string="Następna dostawa (tydzień)")

	# Kody i certyfikaty
	xp_ean_pcs = fields.Char(string="EAN szt.")
	xp_ean_ctns = fields.Char(string="EAN karton")
	xp_cn_code = fields.Char(string="Kod CN")
	xp_eprel = fields.Char(string="EPREL")
	xp_ce = fields.Char(string="CE")
	xp_ukrsepro = fields.Char(string="Ukrsepro")
	xp_lok = fields.Char(string="Lok")
	xp_kgo = fields.Char(string="KGO")

	# Logistyka
	xp_net_weight = fields.Float(string="Waga netto", digits=(16, 5))
	xp_package_width = fields.Float(string="Opakowanie - szerokość")
	xp_package_depth = fields.Float(string="Opakowanie - głębokość")
	xp_package_height = fields.Float(string="Opakowanie - wysokość")
	xp_carton_width = fields.Float(string="Karton - szerokość")
	xp_carton_depth = fields.Float(string="Karton - głębokość")
	xp_carton_height = fields.Float(string="Karton - wysokość")
	xp_packaging_gross_weight = fields.Float(string="Waga brutto opakowania", digits=(16, 3))
	xp_qty_carton = fields.Integer(string="Sztuk w kartonie")
	xp_qty_pallet = fields.Integer(string="Sztuk na palecie")

	# Opisy
	xp_description_short = fields.Html(string="Opis wstępny", translate=True)
	xp_description_details = fields.Text(
		string="Szczegóły opisu (surowe)", translate=True,
		help="Wartość przechowywana bez sanitizacji HTML.",
	)

	# Media
	xp_image_source_url = fields.Char(
		string="Źródło zdjęcia głównego", readonly=True, copy=False,
		help="URL, z którego pobrano bieżące zdjęcie produktu (image_1920). "
			 "Import pobiera zdjęcie ponownie tylko, gdy URL się zmieni.",
	)
	xp_media_ids = fields.One2many("xp.product.media", "product_tmpl_id", string="Media")
	xp_gallery_ids = fields.Many2many(
		"xp.product.media", string="Galeria",
		compute="_compute_xp_gallery_ids",
		help="Media typu 'gallery' - źródło pętli <img> w szablonie XET.",
	)

	@api.depends("xp_media_ids", "xp_media_ids.media_type_code", "xp_media_ids.sequence")
	def _compute_xp_gallery_ids(self):
		for rec in self:
			rec.xp_gallery_ids = rec.xp_media_ids.filtered(
				lambda m: m.media_type_code == "gallery"
			).sorted(lambda m: (m.sequence, m.id))

	# ------------------------------------------------------------------
	# Metody pomocnicze dla szablonu XET (expression)
	# ------------------------------------------------------------------
	def xp_media_url(self, code):
		"""URL pierwszego medium danego typu albo ''."""
		self.ensure_one()
		media = self.xp_media_ids.filtered(lambda m: m.media_type_code == code)
		return media.sorted(lambda m: (m.sequence, m.id))[:1].url or ""

	@api.model
	def xp_fmt_bool(self, value):
		return "t" if value else "f"

	@api.model
	def xp_fmt_num(self, value, digits=None):
		"""Liczba bez zbędnych zer (18.0 -> '18', 0.66 -> '0.66');
		przy `digits` - stała liczba miejsc po przecinku."""
		if value is None or value is False:
			return ""
		if digits is not None:
			return f"{value:.{digits}f}"
		if float(value).is_integer():
			return str(int(value))
		return f"{value:.6f}".rstrip("0").rstrip(".")
#EoF
