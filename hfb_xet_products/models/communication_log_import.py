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


	Import produktów: fetch -> parse -> apply -> finalize.

	fetch    - najstarszy plik z katalogu wejściowego SFTP (wzorzec nazwy),
			   pomijając pliki już zaimportowane (sygnatura nazwa|rozmiar|mtime);
			   pobranie z kontrolą rozmiaru, kopia pliku w logu (file_data).
	parse    - parser strumieniowy (xp_parser) -> xp.staging.line;
			   kontrola wymaganych wartości i duplikatów klucza SKU+EAN w pliku.
	apply    - linie 'draft' partiami do product.template; klucz SKU + EAN
			   (product.product.default_code + barcode): jest -> aktualizacja
			   (tylko zmienione pola), brak -> utworzenie. Savepoint na linię:
			   błąd zapisu oznacza błąd linii, import idzie dalej.
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
from .xp_parser import check_root, iter_products

_logger = logging.getLogger(__name__)

STAGING_CREATE_CHUNK = 500
APPLY_STEP_SECONDS = 60
IMAGES_STEP_SECONDS = 60
IMAGES_BATCH = 20
IMAGE_USER_AGENT = "Odoo-XET-Products/1.0 (+image import)"
# po tylu kolejnych błędach połączenia z danym hostem pozostałe zdjęcia
# z tego hosta są w bieżącym kroku pomijane (bez czekania na timeouty)
IMAGE_HOST_FAIL_LIMIT = 5

# klucze payloadu obsługiwane osobno (nie są polami product.template)
SPECIAL_KEYS = {"sku", "ean", "parent_category", "product_category", "currency", "vat", "media"}


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
	# parse
	# ==================================================================
	def _xp_stage_parse(self):
		path = self._xp_local_file()
		try:
			check_root(path)
		except (ValueError, etree.XMLSyntaxError) as exc:
			raise XpFatalError(f"Niepoprawny plik XML: {exc}") from exc

		Line = self.env["xp.staging.line"].sudo()
		# etap powtarzalny: linie poprzedniej, przerwanej próby są usuwane
		self.env.cr.execute("DELETE FROM xp_staging_line WHERE log_id = %s", (self.id,))

		seen = {}
		buffer = []
		total = errors = 0
		try:
			for result in iter_products(path):
				total += 1
				state = "draft" if result.status == "ok" else "error"
				message = result.reason
				key = (result.sku, result.ean)
				if state == "draft":
					if key in seen:
						state = "error"
						message = f"Duplikat klucza SKU+EAN w pliku (pierwsze wystąpienie: pozycja {seen[key] + 1})."
					else:
						seen[key] = result.index
				errors += state == "error"
				buffer.append({
					"log_id": self.id,
					"sequence": result.index + 1,
					"state": state,
					"sku": result.sku,
					"ean": result.ean,
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
			raise XpFatalError("Plik nie zawiera elementów <Product>.")
		self.write({"xp_count_total": total, "xp_count_error": errors})
		self._xp_post(f"Parsowanie: {total} rekordów, w tym błędnych: {errors}.")
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
		caches = {"categ": {}, "tax": {}, "currency": {}, "media_type": {}}

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
					caches["categ"].clear()  # cache mógł zawierać rekordy wycofane savepointem
					line.write({"state": "error", "error_message": str(exc)})
			self.env.cr.commit()
			if time.monotonic() >= step_end:
				return STAGE_AGAIN

	def _xp_apply_line(self, line, caches):
		config = self.xp_config_id
		data = dict(line.payload or {})
		Template = self.env["product.template"].with_context(
			lang=config.xp_lang or self.env.lang,
			tracking_disable=True, mail_create_nolog=True, mail_notrack=True,
		)
		Product = self.env["product.product"].with_context(active_test=False)

		notes = []
		vals = {k: v for k, v in data.items() if k not in SPECIAL_KEYS}

		categ = self._xp_category(data.get("parent_category"), data.get("product_category"), caches)
		if categ:
			vals["categ_id"] = categ
		if "currency" in data:
			currency = self._xp_currency(data.get("currency"), caches)
			if currency or not data.get("currency"):
				vals["xp_price_currency_id"] = currency
			else:
				notes.append(f"Nieznana waluta {data.get('currency')!r} - pole pominięte.")
		if data.get("vat") is not None:
			tax = self._xp_tax(data.get("vat"), caches)
			if tax:
				vals["taxes_id"] = [tax]
			else:
				notes.append(f"Brak podatku sprzedaży {data.get('vat')}% w firmie - pole pominięte.")

		media = data.get("media") or []

		products = Product.search([
			("default_code", "=", data["sku"]),
			("barcode", "=", data["ean"]),
		], limit=2)
		if len(products) > 1:
			raise Exception(f"Klucz SKU+EAN wskazuje {len(products)} produkty: {products.ids}.")

		if products:
			template = Template.browse(products.product_tmpl_id.id)
			diff = self._xp_diff(template, vals)
			media_changed = self._xp_media_changed(template, media)
			if diff:
				template.write(diff)
			if media_changed:
				self._xp_replace_media(template, media, caches)
			action = "updated" if (diff or media_changed) else "unchanged"
		else:
			create_vals = dict(vals, default_code=data["sku"], barcode=data["ean"])
			create_vals.setdefault("type", "consu")
			if "taxes_id" in create_vals:
				create_vals["taxes_id"] = [Command.set(create_vals["taxes_id"])]
			template = Template.create(create_vals)
			self._xp_replace_media(template, media, caches)
			action = "created"
		return template, action, "\n".join(notes) or False

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

	def _xp_category(self, parent_name, name, caches):
		if not name:
			return False
		cache = caches["categ"]
		key = (parent_name or "", name)
		if key in cache:
			return cache[key]
		Category = self.env["product.category"]
		parent_id = False
		if parent_name:
			parent = Category.search([("name", "=", parent_name), ("parent_id", "=", False)], limit=1)
			if not parent:
				parent = Category.create({"name": parent_name})
			parent_id = parent.id
		categ = Category.search([("name", "=", name), ("parent_id", "=", parent_id)], limit=1)
		if not categ:
			categ = Category.create({"name": name, "parent_id": parent_id})
		cache[key] = categ.id
		return categ.id

	def _xp_currency(self, code, caches):
		if not code:
			return False
		cache = caches["currency"]
		if code not in cache:
			cache[code] = self.env["res.currency"].with_context(active_test=False).search(
				[("name", "=", code)], limit=1).id or False
		return cache[code]

	def _xp_tax(self, rate, caches):
		cache = caches["tax"]
		if rate not in cache:
			company = self.xp_config_id.company_id
			cache[rate] = self.env["account.tax"].search([
				("type_tax_use", "=", "sale"),
				("amount_type", "=", "percent"),
				("amount", "=", float(rate)),
				("company_id", "=", company.id),
			], limit=1).id or False
		return cache[rate]

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
