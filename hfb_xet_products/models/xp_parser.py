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


	Parser pliku XML produktów - bez ORM, sterowany specyfikacją.

	Specyfikację (dict) buduje warstwa ORM z szablonu importu XET
	(communication_log_import._xp_import_spec); od wersji 20.0.0.3.0 nie ma
	tu stałego mapowania pól.

	Specyfikacja:
		{
		  "root": "Document",                # oczekiwany element główny
		  "record": "Product",               # element rekordu (węzeł z pętlą)
		  "entries": [                       # wartości pól
			{"node": "SKU", "rel": "SKU", "attr": None,
			 "target": "default_code",       # pole product.template
			 "relation": None,               # albo (pole_relacji, ścieżka_podrzędna)
			 "ftype": "char",                # typ pola docelowego
			 "role": "key" | "field",
			 "required": True, "strip": True,
			 "bool_true": "t", "bool_false": "f"},
			...],
		  "media": [                         # linki xp.product.media
			{"node": "Product_first_photo", "rel": "Product_first_photo", "attr": None,
			 "code": "first_photo", "loop": False},
			{"node": "img", "rel": "Product_images/img", "code": "gallery", "loop": True,
			 "url_attr": "url", "size_attr": "size"},
			...],
		}

	Wynik dla rekordu (ParseResult.data):
		{"key": {pole: wartość}, "vals": {pole: wartość},
		 "rel": {pole_relacji: {ścieżka_podrzędna: tekst}}, "media": [...]}

	Element nieobecny w rekordzie: pole nie jest zmieniane. Element pusty:
	pole czyszczone (tekst False, liczba 0). Czytanie strumieniowe
	(lxml.iterparse) z czyszczeniem przetworzonych elementów; błędy danych
	są zwracane jako status 'error', bez wyjątków.
"""
from typing import NamedTuple, Optional

from lxml import etree

TEXT_TYPES = ("char", "text", "selection")
TRUE_VALUES = ("t", "true", "1", "y", "yes", "tak")
FALSE_VALUES = ("f", "false", "0", "n", "no", "nie", "")


class ParseResult(NamedTuple):
	index: int
	status: str				# "ok" | "error"
	key: tuple				# wartości klucza w kolejności specyfikacji
	data: Optional[dict]
	reason: Optional[str]


def _raw_value(elem, rel, attr):
	"""Tekst elementu lub wartość atrybutu; None = element/atrybut nieobecny."""
	target = elem if rel in ("", ".") else elem.find(rel)
	if target is None:
		return None
	if attr:
		return target.get(attr)
	return target.text if target.text is not None else ""


def convert(entry, raw):
	"""Konwersja tekstu z pliku wg typu pola docelowego.
	Zwraca wartość Pythona albo rzuca ValueError z opisem."""
	if raw is None:
		return None
	value = raw.strip() if entry.get("strip", True) else raw
	if entry.get("relation"):
		return raw.strip()
	ftype = entry.get("ftype")
	if ftype in TEXT_TYPES:
		return value or False
	if ftype == "html":
		return raw.strip() or False
	if ftype == "boolean":
		low = raw.strip().lower()
		true_v = (entry.get("bool_true") or "").lower()
		false_v = (entry.get("bool_false") or "").lower()
		if low == true_v or low in TRUE_VALUES:
			return True
		if low == false_v or low in FALSE_VALUES:
			return False
		raise ValueError(f"niepoprawna wartość logiczna {raw.strip()!r}")
	if ftype == "integer":
		text = raw.strip()
		return int(float(text.replace(",", "."))) if text else 0
	if ftype in ("float", "monetary"):
		text = raw.strip()
		return float(text.replace(",", ".")) if text else 0.0
	raise ValueError(f"nieobsługiwany typ pola {ftype!r}")


def parse_record(index, elem, spec):
	"""Mapuje jeden element rekordu na ParseResult."""
	key, vals, rel, media, problems = {}, {}, {}, [], []

	for entry in spec["entries"]:
		raw = _raw_value(elem, entry["rel"], entry.get("attr"))
		try:
			value = convert(entry, raw)
		except ValueError as exc:
			problems.append(f"{entry['node']}: {exc}")
			continue
		if entry.get("required") and not value:
			problems.append(f"{entry['node']}: brak wymaganej wartości")
		if value is None:
			continue
		if entry.get("relation"):
			field, sub = entry["relation"]
			rel.setdefault(field, {})[sub] = value
		elif entry.get("role") == "key":
			key[entry["target"]] = value
		else:
			vals[entry["target"]] = value

	for item in spec["media"]:
		if item.get("loop"):
			for pos, node in enumerate(elem.iterfind(item["rel"]), start=1):
				url = (node.get(item.get("url_attr") or "url") or "").strip()
				if url:
					size = node.get(item["size_attr"]) if item.get("size_attr") else None
					media.append({"type": item["code"], "url": url, "size": size or False, "sequence": pos})
		else:
			url = (_raw_value(elem, item["rel"], item.get("attr")) or "").strip()
			if url:
				media.append({"type": item["code"], "url": url, "size": False, "sequence": 10})

	key_tuple = tuple(key.get(e["target"]) for e in spec["entries"] if e.get("role") == "key")
	data = {"key": key, "vals": vals, "rel": rel, "media": media}
	if problems:
		return ParseResult(index, "error", key_tuple, data, "; ".join(problems))
	return ParseResult(index, "ok", key_tuple, data, None)


def iter_records(path, spec):
	"""Strumieniowo zwraca ParseResult dla kolejnych rekordów (index od 0)."""
	context = etree.iterparse(
		path, events=("end",), tag=spec["record"],
		remove_comments=True, resolve_entities=False, no_network=True, huge_tree=True,
	)
	index = 0
	for _event, elem in context:
		yield parse_record(index, elem, spec)
		index += 1
		elem.clear()
		parent = elem.getparent()
		if parent is not None:
			while elem.getprevious() is not None:
				del parent[0]
	del context


def check_root(path, expected="Document"):
	"""Sprawdza element główny pliku. Zwraca nazwę roota albo rzuca ValueError."""
	for _event, elem in etree.iterparse(path, events=("start",), resolve_entities=False, no_network=True):
		tag = etree.QName(elem).localname
		if expected and tag != expected:
			raise ValueError(f"Nieoczekiwany element główny <{tag}> (oczekiwano <{expected}>).")
		return tag
	raise ValueError("Pusty plik XML.")
