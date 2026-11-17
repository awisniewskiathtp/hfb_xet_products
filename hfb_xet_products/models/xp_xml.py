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

	Post-processing XML wygenerowanego przez XET Base - bez ORM.

	`xml.export.template.generate_xml` (XET Base 20.0):
	  - zawsze dodaje `xmlns:xsi`, a przy pustym URI także `xmlns=""`,
	  - nie korzysta z `as_cdata` węzłów,
	  - ma na sztywno pretty_print i deklarację XML.

	Bez zmian w XET Base porządkujemy wynik tutaj: elementy bez przestrzeni
	nazw, bez atrybutów xsi, CDATA dla wskazanych elementów.
"""
from lxml import etree

XSI_NS = "http://www.w3.org/2001/XMLSchema-instance"


def _clean_copy(src, cdata_tags, parent=None):
	tag = etree.QName(src).localname
	dst = etree.Element(tag) if parent is None else etree.SubElement(parent, tag)
	for key, value in src.attrib.items():
		qname = etree.QName(key)
		if qname.namespace == XSI_NS:
			continue
		dst.set(qname.localname, value)
	if tag in cdata_tags:
		dst.text = etree.CDATA(src.text or "")
	elif src.text and src.text.strip():
		dst.text = src.text
	for child in src:
		if not isinstance(child.tag, str):
			continue
		_clean_copy(child, cdata_tags, dst)
	return dst


def clean_xet_xml(xml_bytes, cdata_tags=(), pretty_print=True, encoding="utf-8"):
	"""Zwraca bajty XML bez przestrzeni nazw, z CDATA dla `cdata_tags`."""
	parser = etree.XMLParser(remove_blank_text=True, resolve_entities=False, no_network=True)
	root = etree.fromstring(xml_bytes, parser)
	clean = _clean_copy(root, set(cdata_tags or ()))
	return etree.tostring(
		clean, encoding=encoding, xml_declaration=True, pretty_print=pretty_print,
	)


def validate_xsd(xml_bytes, xsd_bytes):
	"""Walidacja XSD. Zwraca listę błędów (pusta = poprawny)."""
	schema = etree.XMLSchema(etree.fromstring(xsd_bytes))
	doc = etree.fromstring(xml_bytes)
	if schema.validate(doc):
		return []
	return [f"linia {e.line}: {e.message}" for e in schema.error_log]
#EoF
