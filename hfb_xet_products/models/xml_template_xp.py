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


	Rozszerzenie szablonów XET Base na potrzeby XET Products.

	Kod XET Base nie jest zmieniany - moduł dziedziczy modele:
	  - xml.export.template: przeznaczenie szablonu (eksport / import produktów),
	  - xml.export.node: rola węzła przy imporcie (pole, klucz, medium, pomiń)
		i typ medium; pola są widoczne tylko w szablonach importu,
	  - xml.template.exporter: eksport JSON przenosi nowe pola i uzupełnia
		model pętli (loop_model), gdy węzeł ma tylko pole relacji,
	  - xml.template.import_json.wizard: import JSON odtwarza nowe pola
		i pole relacji pętli z plików bez loop_model.

	Szablon importu (xp_usage = import):
	  - model szablonu: product.template,
	  - węzeł z pętlą (loop_mode != none) = element rekordu produktu,
	  - węzeł z wartością: xpath = miejsce w pliku, src_rel_path = pole docelowe,
	  - required_flag = wartość wymagana, fmt_bool_true/false = odczyt pól
		logicznych, fmt_strip = obcinanie białych znaków,
	  - xp_import_role: field | key | media | ignore.
"""
import json
import logging

from odoo import api, fields, models

_logger = logging.getLogger(__name__)

XP_USAGE = [
	("export", "Eksport produktów (XET Products)"),
	("import", "Import produktów (XET Products)"),
]
XP_IMPORT_ROLES = [
	("field", "Pole"),
	("key", "Klucz dopasowania"),
	("media", "Medium (link)"),
	("ignore", "Pomiń"),
]


class XmlExportTemplate(models.Model):
	_inherit = "xml.export.template"

	xp_usage = fields.Selection(
		XP_USAGE, string="Przeznaczenie XET Products", index=True, copy=True,
		help="Szablon używany przez kanały XET Products. Szablony importu "
			 "mają model product.template i opisują odczyt pliku produktów.",
	)


class XmlExportNode(models.Model):
	_inherit = "xml.export.node"

	xp_template_usage = fields.Selection(related="template_id.xp_usage", string="Przeznaczenie szablonu")
	xp_import_role = fields.Selection(
		XP_IMPORT_ROLES, string="Rola przy imporcie", default="field",
		help="Pole: wartość trafia do pola src_rel_path. Klucz: pole jest "
			 "częścią klucza dopasowania produktu. Medium: wartość (lub atrybut "
			 "url elementów pętli) tworzy link xp.product.media danego typu. "
			 "Pomiń: węzeł nie jest czytany.",
	)
	xp_media_type_id = fields.Many2one(
		"xp.media.type", string="Typ medium", ondelete="restrict",
		help="Dla roli 'Medium'.",
	)


class XmlTemplateExporter(models.AbstractModel):
	_inherit = "xml.template.exporter"

	def export_template_json(self, template):
		raw = super().export_template_json(template)
		try:
			data = json.loads(raw)
		except (TypeError, ValueError):
			return raw
		data.setdefault("template", {})["xp_usage"] = template.xp_usage or None
		nodes_by_xpath = {n.xpath: n for n in template.node_ids}

		def walk(items):
			for item in items or []:
				node = nodes_by_xpath.get(item.get("xpath"))
				if node:
					item["xp_import_role"] = node.xp_import_role or None
					item["xp_media_type"] = node.xp_media_type_id.code or None
					# XET Base zapisuje loop_model tylko z loop_model_id;
					# bez niego wizard nie odtworzy pola relacji pętli
					if not item.get("loop_model") and node.loop_rel_field_id:
						item["loop_model"] = node.loop_rel_field_id.model
				walk(item.get("children"))

		walk(data.get("nodes"))
		return json.dumps(data, indent=2, ensure_ascii=False)


class XmlTemplateImportJsonWizard(models.TransientModel):
	_inherit = "xml.template.import_json.wizard"

	def action_import_json(self):
		res = super().action_import_json()
		template_id = isinstance(res, dict) and res.get("res_id")
		if not template_id:
			return res
		try:
			data = json.loads(self.json_file.content.decode("utf-8-sig"))
		except Exception:  # noqa: BLE001 - plik już zaimportowany przez XET Base
			_logger.warning("[XP] Nie udało się ponownie odczytać JSON szablonu.", exc_info=True)
			return res
		template = self.env["xml.export.template"].browse(template_id)
		self._xp_apply_json(template, data)
		return res

	def _xp_apply_json(self, template, data):
		tpl = data.get("template") or {}
		if tpl.get("xp_usage"):
			template.xp_usage = tpl["xp_usage"]
		nodes_by_xpath = {n.xpath: n for n in template.node_ids}
		MediaType = self.env["xp.media.type"].with_context(active_test=False)
		Fields = self.env["ir.model.fields"]
		restored = []

		def walk(items, ctx_model):
			for item in items or []:
				node = nodes_by_xpath.get(item.get("xpath"))
				child_ctx = ctx_model
				if node:
					vals = {}
					if item.get("xp_import_role"):
						vals["xp_import_role"] = item["xp_import_role"]
					if item.get("xp_media_type"):
						mtype = MediaType.search([("code", "=", item["xp_media_type"])], limit=1)
						vals["xp_media_type_id"] = mtype.id or False
					# pliki bez loop_model: pole relacji szukane po nazwie,
					# najpierw na modelu kontekstu (model szablonu / model pętli nadrzędnej)
					rel_name = item.get("loop_rel_field")
					if rel_name and not node.loop_rel_field_id and node.loop_mode in ("one2many", "many2many"):
						candidates = Fields.search([
							("name", "=", rel_name),
							("ttype", "in", ("one2many", "many2many")),
						])
						on_ctx = candidates.filtered(lambda f: f.model == ctx_model)
						field = on_ctx if len(on_ctx) == 1 else (candidates if len(candidates) == 1 else Fields)
						if field:
							vals["loop_rel_field_id"] = field.id
							vals["loop_model_id"] = field.model_id.id
							restored.append(f"{node.xpath} → {field.model}.{rel_name}")
					if vals:
						node.write(vals)
					# model kontekstu dla węzłów potomnych
					if node.loop_mode in ("one2many", "many2many") and node.loop_rel_field_id:
						child_ctx = node.loop_rel_field_id.relation or ctx_model
					elif node.loop_mode == "domain" and node.loop_model_id:
						child_ctx = node.loop_model_id.model
				walk(item.get("children"), child_ctx)

		walk(data.get("nodes"), template.model_id.model)
		if restored:
			template.message_post(body="XET Products: odtworzono pole relacji pętli: " + "; ".join(restored))
		return True
