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


	Eksport produktów: select -> generate -> send -> finalize.

	select   - wybór produktów (domena z konfiguracji) do xp_product_ids;
			   tryb przyrostowy: zmiany (produkt lub media) od xp_cutoff
			   ostatniego poprawnego przebiegu tej konfiguracji.
	generate - xml.export.template.generate_xml(log, in_memory=True) - rekordem
			   startowym jest ten log, szablon iteruje po xp_product_ids;
			   post-processing (bez przestrzeni nazw, CDATA wg as_cdata węzłów),
			   opcjonalna walidacja XSD szablonu.
	send     - SFTP: zapis do <nazwa>.part i rename, kontrola rozmiaru.
	finalize - wynik accepted; xp_cutoff staje się bazą kolejnej delty.
"""
import base64
import contextlib
import logging
import os

from odoo import fields, models
from odoo.tools.safe_eval import safe_eval

from .communication_log import STAGE_STOP, XpFatalError
from .xp_xml import clean_xet_xml, validate_xsd

_logger = logging.getLogger(__name__)

XET_LOGGER = "odoo.addons.hfb_xmlmap_exporter"


@contextlib.contextmanager
def _quiet_xet_logger(level=logging.ERROR):
	"""XET Base loguje każdy węzeł na poziomie INFO - na czas generowania
	katalogu produktów podnosimy próg loggera XET (także WARNING dla
	każdej pustej wartości)."""
	logger = logging.getLogger(XET_LOGGER)
	previous = logger.level
	logger.setLevel(level)
	try:
		yield
	finally:
		logger.setLevel(previous)


class CommunicationLogExport(models.Model):
	_inherit = "communication.log"

	# ==================================================================
	# select
	# ==================================================================
	def _xp_stage_select(self):
		config = self.xp_config_id
		cutoff = fields.Datetime.now()
		try:
			domain = safe_eval(config.export_domain or "[]")
		except Exception as exc:
			raise XpFatalError(f"Niepoprawny filtr produktów: {exc}") from exc

		mode_note = "pełny"
		if config.export_mode == "delta":
			previous = self.search([
				("xp_config_id", "=", config.id),
				("xp_result", "=", "accepted"),
				("xp_cutoff", "!=", False),
				("id", "!=", self.id),
			], order="xp_cutoff desc", limit=1)
			if previous:
				domain = list(domain) + [
					"|",
					("write_date", ">", previous.xp_cutoff),
					("xp_media_ids.write_date", ">", previous.xp_cutoff),
				]
				mode_note = f"przyrostowy od {previous.xp_cutoff} (przebieg {previous.id})"
			else:
				mode_note = "przyrostowy - brak poprzedniej wysyłki, pełny zakres"

		products = self.env["product.template"].search(domain, order="id")
		self.write({
			"xp_product_ids": [(6, 0, products.ids)],
			"xp_cutoff": cutoff,
			"xp_count_total": len(products),
		})
		if not products:
			self.write({
				"xp_result": "nofile",
				"state": "draft",
				"status": "success",
				"message": f"Brak produktów do eksportu (tryb {mode_note}).",
			})
			self._xp_post(f"Brak produktów do eksportu (tryb {mode_note}).")
			return STAGE_STOP
		self._xp_post(f"Wybrano {len(products)} produktów (tryb {mode_note}).")
		return None

	# ==================================================================
	# generate
	# ==================================================================
	def _xp_stage_generate(self):
		config = self.xp_config_id
		template = self.template_id or config.template_id
		if not template:
			raise XpFatalError("Brak szablonu XET w konfiguracji eksportu.")

		with _quiet_xet_logger():
			raw = template.sudo().generate_xml(self, in_memory=True)

		cdata_tags = template.node_ids.filtered("as_cdata").mapped("name")
		xml_bytes = clean_xet_xml(raw, cdata_tags=cdata_tags, pretty_print=template.pretty_print)

		state, validated, validation_message = "generated", False, False
		if config.export_validate_xsd and template.xsd_attachment_id:
			errors = validate_xsd(xml_bytes, template.xsd_attachment_id.raw.content)
			if errors:
				raise XpFatalError("Walidacja XSD nieudana:\n" + "\n".join(errors[:20]))
			state, validated, validation_message = "validated", True, "OK"

		now = fields.Datetime.context_timestamp(self, fields.Datetime.now())
		file_name = (config.export_file_name or "products_{code}_{timestamp}.xml").format(
			code=config.code, timestamp=now.strftime("%Y%m%d%H%M%S"), date=now.strftime("%Y%m%d"),
		)
		self.write({
			"template_id": template.id,
			"file_name": file_name,
			"file_data": base64.b64encode(xml_bytes).decode(),
			"state": state,
			"validated": validated,
			"validation_message": validation_message,
		})
		self._xp_post(f"Wygenerowano {file_name} ({len(xml_bytes)} B, produktów: {len(self.xp_product_ids)}).")
		return None

	# ==================================================================
	# send
	# ==================================================================
	def _xp_stage_send(self):
		config = self.xp_config_id
		if not self.file_data:
			raise XpFatalError("Brak wygenerowanego pliku do wysyłki.")
		content = self.file_data.content
		os.makedirs(config.local_work_dir, exist_ok=True)
		local_path = os.path.join(config.local_work_dir, f"{self.id}_{self.file_name}")
		with open(local_path, "wb") as fh:
			fh.write(content)
		try:
			with config._xp_sftp() as client:
				remote_path, remote_size = client.upload_atomic(local_path, config.sftp_out_dir, self.file_name)
		finally:
			if config.cleanup_work_files and os.path.exists(local_path):
				os.remove(local_path)
		if remote_size != len(content):
			raise Exception(f"Niekompletna wysyłka: lokalnie {len(content)} B, zdalnie {remote_size} B.")
		self.write({
			"state": "sent",
			"status": "success",
			"send_date": fields.Datetime.now(),
			"external_id": remote_path,
			"xp_remote_path": remote_path,
		})
		self._xp_post(f"Wysłano {remote_path} ({remote_size} B).")
		return None

	# ==================================================================
	# finalize
	# ==================================================================
	def _xp_stage_finalize(self):
		if self.xp_config_id.direction != "export":
			return super()._xp_stage_finalize()
		summary = f"Eksport zakończony: {len(self.xp_product_ids)} produktów, plik {self.xp_remote_path}."
		self.write({"xp_result": "accepted", "message": summary})
		self._xp_post(summary)
		return None
#EoF
