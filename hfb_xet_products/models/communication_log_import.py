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


	Import produktów: fetch -> parse -> apply -> images -> finalize.

	fetch    - plik z katalogu wejściowego SFTP (wzorzec nazwy), bez pomijania
			   plików wczytanych wcześniej; z katalogiem archiwum najstarszy,
			   bez niego najnowszy; pobranie z kontrolą rozmiaru, kopia pliku
			   w logu (file_data).
	parse    - specyfikacja z szablonu importu XET (xp_usage = import, kanał
			   albo domyślny xet_template_products_import) -> parser strumieniowy
			   (xp_parser) -> xp.staging.line; kontrola wartości wymaganych
			   (required_flag) i duplikatów klucza w pliku.
	apply    - linie 'draft' partiami do product.template; klucz = węzły z rolą
			   'Klucz dopasowania' (domyślnie SKU + EAN: default_code + barcode):
			   jest -> aktualizacja (tylko zmienione pola), brak -> utworzenie;
			   relacje wyszukiwane po wartościach ścieżek (kategorie tworzone).
			   Savepoint na linię: błąd zapisu oznacza błąd linii.
	images   - (opcjonalnie) pobranie obrazu z medium 'Zdjęcie główne' do
			   image_1920, tylko gdy URL różni się od xp_image_source_url.
			   Odoo 20 nie przyjmuje linku jako obrazu produktu (załącznik typu
			   'url' nie ma zawartości), dlatego obraz jest pobierany.
			   Błąd pobrania = uwaga w linii; produkt zachowuje dotychczasowe
			   zdjęcie, a przy kolejnym imporcie próba jest ponawiana.
	finalize - liczniki, wynik (accepted / manual), przeniesienie pliku
			   zdalnego do archiwum, sprzątanie pliku roboczego.
"""
import base64
import logging
import os
import posixpath
import time

from urllib.parse import urlparse

import requests
from lxml import etree

from odoo import Command, fields, models
from odoo.tools.image import binary_to_image

from .communication_log import STAGE_AGAIN, STAGE_STOP, XpFatalError
from .xp_parser import check_root, iter_records

_logger = logging.getLogger(__name__)

STAGING_CREATE_CHUNK = 500
APPLY_STEP_SECONDS = 60
IMAGES_STEP_SECONDS = 60
IMAGES_BATCH = 20
IMAGE_USER_AGENT = "Odoo-XET-Products/1.0 (+image import)"
# po tylu kolejnych błędach połączenia z danym hostem pozostałe zdjęcia
# z tego hosta są w bieżącym kroku pomijane (bez czekania na timeouty)
IMAGE_HOST_FAIL_LIMIT = 5

# modele relacji, w których brakujący rekord jest tworzony przy imporcie
RELATION_CREATE_MODELS = ("product.category",)


class CommunicationLogImport(models.Model):
	_inherit = "communication.log"

	# ==================================================================
	# fetch
	# ==================================================================
	def _xp_stage_fetch(self):
		"""Pobiera plik z katalogu wejściowego. Plik nie jest pomijany, nawet
		gdy był już wczytany wcześniej (wystawca może publikować zawsze pod
		tą samą nazwą; ponowny import tej samej treści daje 'bez zmian').

		Wybór pliku:
		  - z katalogiem archiwum: najstarszy (kolejka - po imporcie plik
			jest przenoszony, kolejny przebieg weźmie następny),
		  - bez katalogu archiwum: najnowszy (pliki zostają na miejscu,
			więc najstarszy byłby wybierany w nieskończoność).
		"""
		config = self.xp_config_id
		os.makedirs(config.local_work_dir, exist_ok=True)
		with config._xp_sftp() as client:
			entries = client.list_files(config.sftp_in_dir, config.sftp_file_pattern)
			if not entries:
				message = (
					f"Brak plików pasujących do wzorca '{config.sftp_file_pattern or '*'}' "
					f"w katalogu '{config.sftp_in_dir or '.'}'."
				)
				self.write({
					"xp_result": "nofile",
					"state": "received",
					"status": "success",
					"message": message,
				})
				self._xp_post(message)
				return STAGE_STOP

			attr = entries[0] if config.sftp_archive_dir else entries[-1]
			signature = f"{attr.filename}|{attr.st_size}|{int(attr.st_mtime or 0)}"
			remote_path = posixpath.join(config.sftp_in_dir or ".", attr.filename)
			local_path = os.path.join(config.local_work_dir, f"{self.id}_{attr.filename}")
			remote_size = client.download(remote_path, local_path)

		local_size = os.path.getsize(local_path)
		if local_size != remote_size:
			raise Exception(f"Niekompletne pobranie: zdalnie {remote_size} B, lokalnie {local_size} B.")
		with open(local_path, "rb") as fh:
			content = fh.read()
		previous = self.search([
			("xp_config_id", "=", config.id),
			("xp_source_signature", "=", signature),
			("id", "!=", self.id),
		], order="id desc", limit=1)
		self.write({
			"xp_remote_path": remote_path,
			"xp_local_path": local_path,
			"xp_source_signature": signature,
			"file_name": attr.filename,
			"file_data": base64.b64encode(content).decode(),
			"receive_date": fields.Datetime.now(),
		})
		note = f"Pobrano plik {remote_path} ({local_size} B); plików pasujących do wzorca: {len(entries)}."
		if previous:
			note += f"\nTen sam plik (nazwa, rozmiar, data) był wczytany w przebiegu {previous.id} - import mimo to."
		self._xp_post(note)
		self.env.cr.commit()
		return None

	def _xp_local_file(self):
		"""Ścieżka pliku roboczego; odtwarza plik z file_data, gdy go brak
		(np. restart serwera między etapami)."""
		path = self.xp_local_path
		if path and os.path.exists(path):
			return path
		if not self.file_data:
			raise XpFatalError("Brak pliku roboczego i kopii pliku w logu.")
		os.makedirs(self.xp_config_id.local_work_dir, exist_ok=True)
		path = os.path.join(self.xp_config_id.local_work_dir, f"{self.id}_{self.file_name or 'import.xml'}")
		with open(path, "wb") as fh:
			fh.write(self.file_data.content)
		self.write({"xp_local_path": path})
		return path

	# ==================================================================
	# szablon importu -> specyfikacja parsera
	# ==================================================================
	def _xp_import_template(self):
		"""Szablon importu kanału; bez wskazania - domyślny szablon modułu."""
		template = self.xp_config_id.template_id
		if not template or template.xp_usage != "import":
			template = self.env.ref("hfb_xet_products.xet_template_products_import", raise_if_not_found=False)
		if not template:
			raise XpFatalError("Brak szablonu importu XET Products (kanał nie wskazuje szablonu z przeznaczeniem 'import').")
		return template

	def _xp_import_spec(self, template):
		"""Buduje specyfikację parsera (dict bez ORM) z węzłów szablonu importu.

		- rekord: najpłytszy węzeł z pętlą,
		- węzły z value_source = field i src_rel_path: wartości pól,
		  ścieżka przez relację m2o/m2m (np. categ_id.name) = wyszukanie rekordu,
		- rola 'media': pojedynczy link albo pętla elementów z atrybutem url,
		- rola 'ignore' i węzły bez wartości (kontenery) są pomijane.
		"""
		Product = self.env["product.template"]
		nodes = template.node_ids.sorted(lambda n: ((n.xpath or "").count("/"), n.sequence, n.id))
		loops = nodes.filtered(lambda n: n.loop_mode not in (False, "none"))
		if not loops:
			raise XpFatalError(f"Szablon importu {template.name}: brak węzła z pętlą (element rekordu produktu).")
		record = loops[0]
		prefix = (record.xpath or record.name) + "/"
		root = (record.xpath or "").split("/")[0] or template.root_tag
		entries, media, problems = [], [], []

		def location(node):
			rel = (node.xpath or "")[len(prefix):]
			if node.node_kind == "attribute":
				parent, _sep, attr = rel.rpartition("/")
				return parent, attr
			return rel, None

		def in_media_loop(node):
			parent = node.parent_id
			while parent and parent != record:
				if parent.xp_import_role == "media" and parent.loop_mode not in (False, "none"):
					return True
				parent = parent.parent_id
			return False

		for node in nodes:
			if node == record or not (node.xpath or "").startswith(prefix):
				continue
			role = node.xp_import_role or "field"
			if role == "ignore" or in_media_loop(node):
				continue
			rel, attr = location(node)
			if role == "media":
				code = node.xp_media_type_id.code
				if not code:
					problems.append(f"{node.xpath}: rola 'Medium' bez typu medium")
					continue
				if node.loop_mode not in (False, "none"):
					attrs = {c.src_rel_path: c.name for c in node.child_ids if c.node_kind == "attribute" and c.src_rel_path}
					media.append({"node": node.name, "rel": rel, "code": code, "loop": True,
								  "url_attr": attrs.get("url", "url"), "size_attr": attrs.get("size")})
				else:
					media.append({"node": node.name, "rel": rel, "attr": attr, "code": code, "loop": False})
				continue
			if node.value_source != "field" or not node.src_rel_path:
				continue	# węzeł strukturalny
			path = node.src_rel_path.strip()
			first, _sep, sub = path.partition(".")
			field = Product._fields.get(first)
			if field is None:
				problems.append(f"{node.xpath}: pole {first!r} nie istnieje w product.template")
				continue
			entry = {
				"node": node.name, "rel": rel, "attr": attr, "target": first, "role": role,
				"required": bool(node.required_flag), "strip": bool(node.fmt_strip),
				"bool_true": node.fmt_bool_true, "bool_false": node.fmt_bool_false,
				"ftype": field.type, "relation": None,
			}
			if field.type in ("many2one", "many2many"):
				if not sub:
					problems.append(f"{node.xpath}: relacja {first} wymaga pola wyszukiwania, np. {first}.name")
					continue
				if role == "key":
					problems.append(f"{node.xpath}: klucz nie może być ścieżką przez relację")
					continue
				entry["relation"] = (first, sub)
			elif sub:
				problems.append(f"{node.xpath}: {path!r} - pole {first} nie jest relacją")
				continue
			elif field.type in ("one2many", "binary", "image", "json", "properties"):
				problems.append(f"{node.xpath}: nieobsługiwany typ pola {first} ({field.type})")
				continue
			entries.append(entry)

		if not any(e["role"] == "key" for e in entries):
			problems.append("brak węzłów z rolą 'Klucz dopasowania'")
		if problems:
			raise XpFatalError(f"Szablon importu {template.name}:\n" + "\n".join(problems))
		return {"root": root, "record": record.name, "entries": entries, "media": media}

	# ==================================================================
	# parse
	# ==================================================================
	def _xp_stage_parse(self):
		path = self._xp_local_file()
		template = self._xp_import_template()
		spec = self._xp_import_spec(template)
		try:
			check_root(path, spec["root"])
		except (ValueError, etree.XMLSyntaxError) as exc:
			raise XpFatalError(f"Niepoprawny plik XML: {exc}") from exc

		Line = self.env["xp.staging.line"].sudo()
		# etap powtarzalny: linie poprzedniej, przerwanej próby są usuwane
		self.env.cr.execute("DELETE FROM xp_staging_line WHERE log_id = %s", (self.id,))

		key_names = [e["node"] for e in spec["entries"] if e["role"] == "key"]
		seen = {}
		buffer = []
		total = errors = 0
		try:
			for result in iter_records(path, spec):
				total += 1
				state = "draft" if result.status == "ok" else "error"
				message = result.reason
				if state == "draft":
					if result.key in seen:
						state = "error"
						message = (f"Duplikat klucza {'+'.join(key_names)} w pliku "
								   f"(pierwsze wystąpienie: pozycja {seen[result.key] + 1}).")
					else:
						seen[result.key] = result.index
				errors += state == "error"
				key = result.data.get("key") or {}
				values = [v for v in result.key if v]
				buffer.append({
					"log_id": self.id,
					"sequence": result.index + 1,
					"state": state,
					"sku": key.get("default_code") or (values[0] if values else False),
					"ean": key.get("barcode") or (values[1] if len(values) > 1 else False),
					"payload": result.data,
					"error_message": message,
				})
				if len(buffer) >= STAGING_CREATE_CHUNK:
					Line.create(buffer)
					buffer = []
			if buffer:
				Line.create(buffer)
		except etree.XMLSyntaxError as exc:
			raise XpFatalError(f"Błąd składni XML: {exc}") from exc

		if not total:
			raise XpFatalError(f"Plik nie zawiera elementów <{spec['record']}>.")
		self.write({"xp_count_total": total, "xp_count_error": errors, "template_id": template.id})
		self._xp_post(f"Parsowanie (szablon {template.name}): {total} rekordów, w tym błędnych: {errors}.")
		self.env.cr.commit()
		return None

	# ==================================================================
	# apply
	# ==================================================================
	def _xp_stage_apply(self):
		config = self.xp_config_id
		Line = self.env["xp.staging.line"].sudo()
		batch_size = max(config.batch_size, 1)
		step_end = time.monotonic() + APPLY_STEP_SECONDS
		caches = {"rel": {}, "media_type": {}}

		while True:
			lines = Line.search([("log_id", "=", self.id), ("state", "=", "draft")],
								order="sequence, id", limit=batch_size)
			if not lines:
				return None
			for line in lines:
				try:
					with self.env.cr.savepoint():
						product, action, note = self._xp_apply_line(line, caches)
					line.write({
						"state": "processed", "action": action,
						"product_tmpl_id": product.id, "error_message": note,
					})
				except Exception as exc:  # noqa: BLE001 - błąd linii, import idzie dalej
					# pamięć podręczna mogła zawierać rekordy wycofane savepointem
					caches["rel"].clear()
					caches["media_type"].clear()
					line.write({"state": "error", "error_message": str(exc)})
			self.env.cr.commit()
			if time.monotonic() >= step_end:
				return STAGE_AGAIN

	def _xp_apply_line(self, line, caches):
		config = self.xp_config_id
		data = dict(line.payload or {})
		if "vals" not in data:
			raise Exception("Linia w formacie sprzed wersji 20.0.0.3.0 - uruchom przebieg importu ponownie.")
		Template = self.env["product.template"].with_context(
			lang=config.xp_lang or self.env.lang,
			tracking_disable=True, mail_create_nolog=True, mail_notrack=True,
		)
		notes = []
		vals = dict(data.get("vals") or {})
		for fname, subs in (data.get("rel") or {}).items():
			value = self._xp_resolve_relation(fname, subs, caches, notes)
			if value is not None:
				vals[fname] = value
		key = data.get("key") or {}
		media = data.get("media") or []

		templates = self._xp_find_by_key(key)
		if len(templates) > 1:
			raise Exception(f"Klucz {key} wskazuje {len(templates)} produkty: {templates.ids}.")

		if templates:
			template = Template.browse(templates.id)
			diff = self._xp_diff(template, vals)
			media_changed = self._xp_media_changed(template, media)
			if diff:
				template.write(diff)
			if media_changed:
				self._xp_replace_media(template, media, caches)
			action = "updated" if (diff or media_changed) else "unchanged"
		else:
			create_vals = dict(vals, **key)
			create_vals.setdefault("type", "consu")
			for fname, value in list(create_vals.items()):
				if Template._fields[fname].type == "many2many":
					create_vals[fname] = [Command.set(value or [])]
			template = Template.create(create_vals)
			self._xp_replace_media(template, media, caches)
			action = "created"
		return template, action, "\n".join(notes) or False

	def _xp_find_by_key(self, key):
		"""Szablony produktów pasujące do klucza. Gdy wszystkie pola klucza
		istnieją na product.product (np. default_code, barcode), szukanie
		odbywa się po wariantach."""
		if not key:
			raise Exception("Pusty klucz dopasowania.")
		domain = [(name, "=", value) for name, value in key.items()]
		Variant = self.env["product.product"].with_context(active_test=False)
		if all(name in Variant._fields and Variant._fields[name].store for name in key):
			return Variant.search(domain, limit=2).product_tmpl_id
		return self.env["product.template"].with_context(active_test=False).search(domain, limit=2)

	def _xp_relation_domain(self, comodel):
		"""Warunki dodatkowe wyszukiwania rekordu relacji."""
		if comodel == "account.tax":
			return [("type_tax_use", "=", "sale"), ("amount_type", "=", "percent"),
					("company_id", "=", self.xp_config_id.company_id.id)]
		return []

	def _xp_resolve_relation(self, fname, subs, caches, notes):
		"""Wartość pola relacji z wartości tekstowych ścieżek podrzędnych.

		Zwraca: id (many2one), listę id (many2many), False (wyczyść)
		albo None (nie zmieniaj pola).
		- model z parent_id i dozwolonym tworzeniem (product.category):
		  ścieżki name / parent_id.name / parent_id.parent_id.name … budują
		  hierarchię; brakujące rekordy są tworzone; pusta nazwa = bez zmian,
		- pozostałe modele: wyszukanie po wszystkich ścieżkach; brak rekordu =
		  uwaga w linii i pole bez zmian; puste wartości: many2one czyszczone,
		  many2many bez zmian.
		"""
		field = self.env["product.template"]._fields[fname]
		comodel = field.comodel_name
		Model = self.env[comodel].with_context(active_test=False)
		cache_key = (comodel, tuple(sorted(subs.items())))
		if cache_key in caches["rel"]:
			return caches["rel"][cache_key]

		result = None
		if comodel in RELATION_CREATE_MODELS and "parent_id" in Model._fields:
			levels = sorted(((sub.count("parent_id."), sub.rsplit(".", 1)[-1], value)
							 for sub, value in subs.items()), reverse=True)
			if any(depth == 0 and value for depth, _leaf, value in levels):
				parent_id = False
				for _depth, leaf, value in levels:
					if not value:
						continue
					record = Model.search([(leaf, "=", value), ("parent_id", "=", parent_id)], limit=1)
					if not record:
						record = Model.create({leaf: value, "parent_id": parent_id})
					parent_id = record.id
				result = parent_id
		elif not any(subs.values()):
			result = False if field.type == "many2one" else None
		else:
			domain = list(self._xp_relation_domain(comodel))
			for sub, value in subs.items():
				leaf = Model._fields.get(sub.split(".")[0])
				if leaf is not None and leaf.type in ("float", "monetary"):
					value = float(value.replace(",", "."))
				elif leaf is not None and leaf.type == "integer":
					value = int(float(value.replace(",", ".")))
				domain.append((sub, "=", value))
			record = Model.search(domain, limit=1)
			if record:
				result = record.id if field.type == "many2one" else [record.id]
			else:
				notes.append(f"{fname}: brak rekordu {comodel} dla {subs} - pole pominięte.")
				return None
		caches["rel"][cache_key] = result
		return result

	# ------------------------------------------------------------------
	def _xp_diff(self, record, vals):
		"""Zwraca tylko te wartości, które różnią się od bieżących."""
		diff = {}
		for key, value in vals.items():
			field = record._fields.get(key)
			if field is None:
				continue
			current = record[key]
			if field.type == "many2one":
				if (current.id or False) != (value or False):
					diff[key] = value
			elif field.type in ("many2many", "one2many"):
				if set(current.ids) != set(value or []):
					diff[key] = [(6, 0, list(value or []))]
			else:
				new = field.convert_to_record(field.convert_to_cache(value, record), record)
				if field.type == "html":
					changed = str(new or "") != str(current or "")
				elif field.type == "float":
					changed = abs((new or 0.0) - (current or 0.0)) > 1e-9
				else:
					changed = (new or False) != (current or False)
				if changed:
					diff[key] = value
		return diff

	def _xp_media_changed(self, template, media):
		current = sorted(
			(m.media_type_code, m.url, m.size or False, m.sequence) for m in template.xp_media_ids
		)
		new = sorted((m["type"], m["url"], m.get("size") or False, m.get("sequence") or 10) for m in media)
		return current != new

	def _xp_replace_media(self, template, media, caches):
		template.xp_media_ids.unlink()
		vals_list = []
		for item in media:
			type_id = self._xp_media_type(item["type"], caches)
			vals_list.append({
				"product_tmpl_id": template.id,
				"media_type_id": type_id,
				"url": item["url"],
				"size": item.get("size") or False,
				"sequence": item.get("sequence") or 10,
			})
		if vals_list:
			self.env["xp.product.media"].create(vals_list)

	def _xp_media_type(self, code, caches):
		cache = caches["media_type"]
		if code not in cache:
			mtype = self.env["xp.media.type"].with_context(active_test=False).search([("code", "=", code)], limit=1)
			if not mtype:
				mtype = self.env["xp.media.type"].create({"code": code, "name": code})
			cache[code] = mtype.id
		return cache[code]

	# ==================================================================
	# images
	# ==================================================================
	def _xp_stage_images(self):
		config = self.xp_config_id
		if not config.image_download:
			return None
		Line = self.env["xp.staging.line"].sudo()
		code = config.image_media_code or "first_photo"
		timeout = max(config.image_timeout, 1)
		max_bytes = max(config.image_max_mb, 1) * 1024 * 1024
		step_end = time.monotonic() + IMAGES_STEP_SECONDS
		host_failures = {}

		while True:
			lines = Line.search([
				("log_id", "=", self.id),
				("state", "=", "processed"),
				("image_state", "=", False),
				("product_tmpl_id", "!=", False),
			], order="sequence, id", limit=IMAGES_BATCH)
			if not lines:
				return None
			for line in lines:
				template = line.product_tmpl_id.with_context(
					tracking_disable=True, mail_notrack=True, active_test=False,
				)
				url = template.xp_media_url(code)
				if not url or (url == template.xp_image_source_url and template.image_128):
					line.image_state = "skipped"
					continue
				host = urlparse(url).netloc.lower()
				try:
					if host_failures.get(host, 0) >= IMAGE_HOST_FAIL_LIMIT:
						raise ConnectionError(
							f"pominięto - {IMAGE_HOST_FAIL_LIMIT} kolejnych błędów połączenia z {host}")
					try:
						content = self._xp_download_image(url, timeout, max_bytes)
					except (requests.ConnectionError, requests.Timeout):
						host_failures[host] = host_failures.get(host, 0) + 1
						raise
					host_failures[host] = 0
					with self.env.cr.savepoint():
						template.write({
							"image_1920": base64.b64encode(content).decode(),
							"xp_image_source_url": url,
						})
					line.image_state = "done"
				except Exception as exc:  # noqa: BLE001 - uwaga w linii, import idzie dalej
					note = f"Zdjęcie główne ({url}): {exc}"
					line.write({
						"image_state": "error",
						"error_message": "\n".join(filter(None, [line.error_message, note])),
					})
			self.env.cr.commit()
			if time.monotonic() >= step_end:
				return STAGE_AGAIN

	@staticmethod
	def _xp_download_image(url, timeout, max_bytes):
		"""Pobiera obraz (HTTP/HTTPS) z limitem czasu i rozmiaru i sprawdza,
		czy to obraz rastrowy. Pole Image w Odoo 20 nie przetwarza treści
		zaczynającej się od '<' (traktuje ją jak SVG) i zapisałoby np. stronę
		HTML błędu podaną z nagłówkiem image/jpeg - stąd własna kontrola PIL."""
		if not url.lower().startswith(("http://", "https://")):
			raise ValueError("nieobsługiwany adres (wymagany http/https)")
		with requests.get(url, timeout=timeout, stream=True,
						  headers={"User-Agent": IMAGE_USER_AGENT}) as response:
			response.raise_for_status()
			ctype = (response.headers.get("Content-Type") or "").split(";")[0].strip().lower()
			if ctype and not ctype.startswith("image/"):
				raise ValueError(f"serwer zwrócił {ctype!r}, a nie obraz")
			chunks, size = [], 0
			for chunk in response.iter_content(64 * 1024):
				size += len(chunk)
				if size > max_bytes:
					raise ValueError(f"obraz większy niż {max_bytes // (1024 * 1024)} MB")
				chunks.append(chunk)
		content = b"".join(chunks)
		if not content:
			raise ValueError("pusta odpowiedź")
		try:
			binary_to_image(content).verify()
		except Exception as exc:
			raise ValueError("pobrana treść nie jest obrazem") from exc
		return content

	# ==================================================================
	# finalize
	# ==================================================================
	def _xp_stage_finalize(self):
		if self.xp_config_id.direction != "import":
			return super()._xp_stage_finalize()
		config = self.xp_config_id
		self.env.cr.execute(
			"""
			SELECT state, action, count(*) FROM xp_staging_line
			 WHERE log_id = %s GROUP BY state, action
			""",
			(self.id,),
		)
		counts = {"created": 0, "updated": 0, "unchanged": 0, "error": 0}
		for state, action, cnt in self.env.cr.fetchall():
			if state == "error":
				counts["error"] += cnt
			elif action in counts:
				counts[action] += cnt
		result = "accepted" if not counts["error"] else "manual"

		self.env.cr.execute(
			"""
			SELECT image_state, count(*) FROM xp_staging_line
			 WHERE log_id = %s AND image_state IS NOT NULL GROUP BY image_state
			""",
			(self.id,),
		)
		images = dict(self.env.cr.fetchall())

		moved = False
		if config.sftp_archive_dir and self.xp_remote_path:
			moved = self._xp_move_remote(config.sftp_archive_dir)
		self._xp_cleanup_local()

		summary = (
			f"Import zakończony: utworzone {counts['created']}, zaktualizowane {counts['updated']}, "
			f"bez zmian {counts['unchanged']}, błędy {counts['error']}."
		)
		if config.image_download:
			summary += (
				f"\nZdjęcia główne: pobrane {images.get('done', 0)}, bez zmian {images.get('skipped', 0)}, "
				f"błędy pobrania {images.get('error', 0)}."
			)
		if moved:
			summary += f"\nPlik przeniesiony do: {moved}"
		self.write({
			"xp_count_created": counts["created"],
			"xp_count_updated": counts["updated"],
			"xp_count_unchanged": counts["unchanged"],
			"xp_count_error": counts["error"],
			"xp_count_images": images.get("done", 0),
			"xp_count_image_errors": images.get("error", 0),
			"xp_result": result,
			"state": "received",
			"status": "success" if result == "accepted" else "warning",
			"message": summary,
		})
		self._xp_post(summary)
		return None

	def _xp_move_remote(self, target_dir):
		config = self.xp_config_id
		with config._xp_sftp() as client:
			try:
				client.sftp.stat(self.xp_remote_path)
			except IOError:
				return False	# plik już przeniesiony (ponowiony etap)
			return client.move(self.xp_remote_path, target_dir)

	def _xp_cleanup_local(self):
		if self.xp_config_id.cleanup_work_files and self.xp_local_path and os.path.exists(self.xp_local_path):
			os.remove(self.xp_local_path)

	def _xp_on_run_error(self):
		res = super()._xp_on_run_error()
		if self.xp_config_id.direction == "import":
			if self.xp_config_id.sftp_error_dir and self.xp_remote_path and self.xp_stage in ("fetch", "parse"):
				moved = self._xp_move_remote(self.xp_config_id.sftp_error_dir)
				if moved:
					self._xp_post(f"Plik przeniesiony do katalogu błędów: {moved}")
			self._xp_cleanup_local()
		return res
#EoF
