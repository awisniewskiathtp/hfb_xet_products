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


	Parser pliku XML produktów - bez ORM.

	Format (plik ADLER, np. 45ca7c3c-794.xml):
		<Document>
		  <Product> ... 56 pól ... <Product_images><img url=".." size=".."/></Product_images>
		</Product>
		...

	Czytanie strumieniowe (lxml.iterparse) z czyszczeniem przetworzonych
	elementów. Każdy <Product> daje jeden ParseResult. Błędy danych nie
	rzucają wyjątków - są zwracane jako status 'error' z opisem.

	Mapowanie pól: FIELD_SPEC (element XML -> klucz payloadu -> rodzaj).
	Klucze payloadu są nazwami pól Odoo, poza kluczami specjalnymi
	obsługiwanymi w etapie apply (sku, ean, parent_category, product_category,
	currency, vat, media).
"""
from typing import NamedTuple, Optional

from lxml import etree

PRODUCT_TAG = "Product"

# (element XML, klucz payloadu, rodzaj)
# rodzaje: str, text, int, float, bool_tf, html, raw
FIELD_SPEC = [
	# identyfikacja
	("SKU", "sku", "str"),
	("EAN", "ean", "str"),
	("Symbol", "xp_symbol", "str"),
	("Name_WMS", "xp_name_wms", "str"),
	("Link", "xp_link", "str"),
	# producent i marka
	("Producer", "xp_producer", "str"),
	("Producer_data", "xp_producer_data", "text"),
	("Product_brand", "xp_brand", "str"),
	("Safety", "xp_safety", "text"),
	("Product_info", "xp_product_info", "text"),
	# kategorie, ceny, podatek
	("Parent_category", "parent_category", "str"),
	("Product_category", "product_category", "str"),
	("Price", "xp_price", "float"),
	("Currency", "currency", "str"),
	("SRP_price", "list_price", "float"),
	("VAT_PL", "vat", "float"),
	# flagi
	("News", "xp_news", "bool_tf"),
	("Top", "xp_top", "bool_tf"),
	("Hit", "xp_hit", "bool_tf"),
	("Promo", "xp_promo", "bool_tf"),
	("Outlet", "xp_outlet", "bool_tf"),
	("Hidden_eu", "xp_hidden_eu", "bool_tf"),
	# sortowanie i dostępność
	("Product_sort_order", "xp_sort_order", "int"),
	("Product_sort_order_form", "xp_sort_order_form", "int"),
	("Available_in_warehouse", "xp_available", "bool_tf"),
	("Available_in_warehouse_pcs", "xp_available_qty", "float"),
	("Next_delivery", "xp_next_delivery", "str"),
	# kody i certyfikaty
	("EAN_PCS", "xp_ean_pcs", "str"),
	("EAN_CTNS", "xp_ean_ctns", "str"),
	("CN_code", "xp_cn_code", "str"),
	("EPREL", "xp_eprel", "str"),
	("CE", "xp_ce", "str"),
	("Ukrsepro", "xp_ukrsepro", "str"),
	("Lok", "xp_lok", "str"),
	("KGO", "xp_kgo", "str"),
	# logistyka
	("Product_netto_weight", "xp_net_weight", "float"),
	("Product_gross_weight", "weight", "float"),
	("Package_width", "xp_package_width", "float"),
	("Package_depth", "xp_package_depth", "float"),
	("Package_height", "xp_package_height", "float"),
	("Carton_width", "xp_carton_width", "float"),
	("Carton_depth", "xp_carton_depth", "float"),
	("Carton_height", "xp_carton_height", "float"),
	("Packaging_gross_weight", "xp_packaging_gross_weight", "float"),
	("Number_of_pieces_in_carton", "xp_qty_carton", "int"),
	("Number_of_pieces_on_one_pallet", "xp_qty_pallet", "int"),
	# nazwy i opisy
	("Product_name", "name", "str"),
	("Product_preliminary_description", "xp_description_short", "html"),
	("Product_description", "description", "html"),
	("Product_description_details", "xp_description_details", "raw"),
]

# Pojedyncze linki -> typ medium (xp.media.type.code)
MEDIA_SPEC = [
	("Product_first_photo", "first_photo"),
	("Product_package_photo", "package_photo"),
	("Product_youtube_link", "youtube"),
	("Link_to_product", "product_page"),
	("Manual", "manual"),
]
GALLERY_TAG = "Product_images"
GALLERY_ITEM_TAG = "img"
GALLERY_CODE = "gallery"

REQUIRED_KEYS = ("sku", "ean", "name")


class ParseResult(NamedTuple):
	index: int
	status: str				# "ok" | "error"
	sku: Optional[str]
	ean: Optional[str]
	data: Optional[dict]
	reason: Optional[str]


def _text(elem):
	if elem is None:
		return None
	return elem.text if elem.text is not None else ""


def _convert(kind, raw):
	"""Zwraca wartość Pythona albo rzuca ValueError z opisem."""
	if raw is None:
		return None
	if kind == "raw":
		return raw if raw != "" else False
	if kind == "html":
		value = raw.strip()
		return value or False
	value = raw.strip()
	if kind in ("str", "text"):
		return value or False
	if kind == "bool_tf":
		low = value.lower()
		if low in ("t", "true", "1", "y", "yes"):
			return True
		if low in ("f", "false", "0", "n", "no", ""):
			return False
		raise ValueError(f"niepoprawna wartość logiczna {value!r}")
	if kind == "int":
		if value == "":
			return 0
		return int(float(value.replace(",", ".")))
	if kind == "float":
		if value == "":
			return 0.0
		return float(value.replace(",", "."))
	raise ValueError(f"nieznany rodzaj pola {kind!r}")


def parse_product(index, elem):
	"""Mapuje jeden element <Product> na ParseResult."""
	data = {}
	problems = []

	for tag, key, kind in FIELD_SPEC:
		raw = _text(elem.find(tag))
		try:
			value = _convert(kind, raw)
		except ValueError as exc:
			problems.append(f"{tag}: {exc}")
			continue
		if value is not None:
			data[key] = value

	media = []
	for tag, code in MEDIA_SPEC:
		url = (_text(elem.find(tag)) or "").strip()
		if url:
			media.append({"type": code, "url": url, "size": False, "sequence": 10})
	gallery = elem.find(GALLERY_TAG)
	if gallery is not None:
		for pos, img in enumerate(gallery.iter(GALLERY_ITEM_TAG), start=1):
			url = (img.get("url") or "").strip()
			if url:
				media.append({
					"type": GALLERY_CODE, "url": url,
					"size": img.get("size") or False, "sequence": pos,
				})
	data["media"] = media

	sku = data.get("sku") or None
	ean = data.get("ean") or None
	for key in REQUIRED_KEYS:
		if not data.get(key):
			problems.append(f"brak wymaganej wartości '{key}'")

	text_values = [v for v in data.values() if isinstance(v, str)]
	if any("\x00" in v for v in text_values):
		problems.append("znak NUL w danych")

	if problems:
		return ParseResult(index, "error", sku, ean, data, "; ".join(problems))
	return ParseResult(index, "ok", sku, ean, data, None)


def iter_products(path):
	"""Strumieniowo zwraca ParseResult dla kolejnych <Product> (index od 0)."""
	context = etree.iterparse(
		path, events=("end",), tag=PRODUCT_TAG,
		remove_comments=True, resolve_entities=False, no_network=True,
		huge_tree=True,
	)
	index = 0
	for _event, elem in context:
		yield parse_product(index, elem)
		index += 1
		elem.clear()
		parent = elem.getparent()
		if parent is not None:
			while elem.getprevious() is not None:
				del parent[0]
	del context


def check_root(path):
	"""Sprawdza element główny pliku. Zwraca nazwę roota albo rzuca ValueError."""
	for _event, elem in etree.iterparse(path, events=("start",), resolve_entities=False, no_network=True):
		tag = etree.QName(elem).localname
		if tag != "Document":
			raise ValueError(f"Nieoczekiwany element główny <{tag}> (oczekiwano <Document>).")
		return tag
	raise ValueError("Pusty plik XML.")
#EoF
