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

	communication.provider.xet_products - konfiguracja kanału (plugin providera).

	Jedna konfiguracja = jeden kanał:
	  - direction = import: źródło pliku produktów (SFTP: katalog wejściowy,
		archiwum, błędy),
	  - direction = export: jeden odbiorca (SFTP: katalog docelowy, szablon XET,
		nazwa pliku, tryb pełny/przyrostowy).

	Parametry SFTP jak w hfb_apds. Częstotliwość przebiegów wyznacza interwał
	crona inicjującego (data/ir_cron.xml) - bez pól harmonogramu na konfiguracji.

	Kontrakt providera (communication.provider w XET Base):
		authenticate(**kwargs), send_document(log), fetch_documents(log),
		get_status(log), provider_test()
"""
import logging

from odoo import _, api, fields, models
from odoo.addons.base.models.res_partner import _lang_get
from odoo.exceptions import UserError

from .xp_sftp import XpSftpClient

_logger = logging.getLogger(__name__)

ACTIVE_RESULTS = ("pending", "running")


class CommunicationProviderXetProducts(models.Model):
	_name = "communication.provider.xet_products"
	_description = "XET Products - konfiguracja kanału"
	_inherit = ["mail.thread"]
	_order = "direction, name"

	name = fields.Char(string="Nazwa", required=True, tracking=True)
	code = fields.Char(string="Kod", required=True, tracking=True)
	active = fields.Boolean(default=True, tracking=True)
	company_id = fields.Many2one(
		"res.company", required=True, default=lambda self: self.env.company,
		ondelete="cascade",
	)
	direction = fields.Selection(
		[("import", "Import produktów"), ("export", "Eksport produktów")],
		string="Kierunek", required=True, default="export", tracking=True,
	)
	provider_id = fields.Many2one(
		"communication.provider", string="Provider XET",
		compute="_compute_provider_id",
	)

	# ------------------------------------------------------------------
	# SFTP (jak hfb_apds)
	# ------------------------------------------------------------------
	sftp_host = fields.Char(string="Host SFTP", required=True)
	sftp_port = fields.Integer(string="Port SFTP", default=22)
	sftp_username = fields.Char(string="Użytkownik SFTP")
	sftp_password = fields.Char(string="Hasło SFTP", groups="base.group_system")
	sftp_private_key = fields.Text(
		string="Klucz prywatny SFTP", groups="base.group_system",
		help="Alternatywa dla hasła. Obsługiwane: Ed25519, ECDSA, RSA.",
	)
	sftp_key_passphrase = fields.Char(string="Hasło klucza", groups="base.group_system")
	sftp_host_fingerprint = fields.Char(
		string="Odcisk klucza hosta",
		help="Format SHA256:... (ssh-keygen -l). Puste = bez weryfikacji. "
			 "Przycisk 'Test połączenia' pokazuje odcisk serwera.",
	)
	sftp_timeout = fields.Integer(string="Timeout [s]", default=30)

	# Import
	sftp_in_dir = fields.Char(string="Katalog wejściowy", default="in")
	sftp_file_pattern = fields.Char(string="Wzorzec nazwy pliku", default="*.xml")
	sftp_archive_dir = fields.Char(
		string="Katalog archiwum",
		help="Po imporcie plik jest przenoszony tutaj, a pliki są wczytywane "
			 "od najstarszego. Puste = plik zostaje na miejscu i każdy przebieg "
			 "wczytuje najnowszy plik (także ten sam ponownie).",
	)
	sftp_error_dir = fields.Char(
		string="Katalog błędów",
		help="Plik, którego nie udało się wczytać, jest przenoszony tutaj. "
			 "Puste = plik zostaje na miejscu.",
	)
	xp_lang = fields.Selection(
		_lang_get, string="Język danych",
		default=lambda self: "pl_PL" if "pl_PL" in dict(self.env["res.lang"].get_installed()) else self.env.lang,
		help="Język, w którym zapisywane są pola tłumaczone (nazwa, opisy).",
	)

	image_download = fields.Boolean(
		string="Pobieraj zdjęcie główne", default=True,
		help="Etap 'images': pobiera obraz z medium 'Zdjęcie główne' "
			 "(first_photo) do zdjęcia produktu (image_1920), tylko gdy "
			 "URL się zmienił. Błąd pobrania nie blokuje produktu.",
	)
	image_media_code = fields.Char(string="Typ medium zdjęcia głównego", default="first_photo")
	image_timeout = fields.Integer(string="Timeout pobierania [s]", default=15)
	image_max_mb = fields.Integer(string="Maks. rozmiar zdjęcia [MB]", default=10)

	# Eksport
	sftp_out_dir = fields.Char(string="Katalog docelowy", default="out")
	export_file_name = fields.Char(
		string="Nazwa pliku eksportu", default="products_{code}_{timestamp}.xml",
		help="Zmienne: {code}, {timestamp} (RRRRMMDDGGMMSS), {date} (RRRRMMDD).",
	)
	template_id = fields.Many2one(
		"xml.export.template", string="Szablon XET",
		domain="[('xp_usage', '=', direction)]",
		help="Eksport: szablon z rekordem startowym communication.log (pętla po "
			 "polu xp_product_ids). Import: szablon z modelem product.template "
			 "opisujący odczyt pliku; puste = domyślny szablon importu modułu.",
	)
	export_mode = fields.Selection(
		[("full", "Pełny"), ("delta", "Przyrostowy (zmiany od ostatniej wysyłki)")],
		string="Tryb eksportu", default="full", required=True,
	)
	export_domain = fields.Char(
		string="Filtr produktów", default="[('sale_ok', '=', True)]",
		help="Domena product.template wybierająca produkty do eksportu.",
	)
	export_validate_xsd = fields.Boolean(
		string="Waliduj wg XSD szablonu", default=True,
		help="Działa, gdy szablon ma podpięty plik XSD.",
	)

	# Przetwarzanie
	local_work_dir = fields.Char(string="Katalog roboczy (lokalny)", default="/tmp/xet_products")
	cleanup_work_files = fields.Boolean(string="Usuwaj pliki robocze", default=True)
	batch_size = fields.Integer(string="Rozmiar partii (apply)", default=200)
	lock_timeout_minutes = fields.Integer(
		string="Limit blokady etapu [min]", default=30,
		help="Etap w stanie 'running' dłużej niż limit uznaje się za "
			 "przerwany (np. ubity cron) i ponawia.",
	)
	max_attempts = fields.Integer(string="Maks. liczba prób etapu", default=3)
	retry_delay_minutes = fields.Integer(string="Odstęp ponowienia [min]", default=5)

	log_ids = fields.One2many("communication.log", "xp_config_id", string="Przebiegi")
	log_count = fields.Integer(compute="_compute_log_count")

	_code_company_unique = models.Constraint(
		"UNIQUE(code, company_id)",
		"Kod konfiguracji musi być unikalny w firmie.",
	)

	# ------------------------------------------------------------------
	def _compute_provider_id(self):
		Provider = self.env["communication.provider"].with_context(active_test=False)
		for config in self:
			config.provider_id = Provider.search([
				("provider_type", "=", "xet_products"),
				("provider_config_id", "=", config.id),
			], limit=1) if config.id else False

	def _compute_log_count(self):
		for config in self:
			config.log_count = len(config.log_ids)

	@api.model_create_multi
	def create(self, vals_list):
		configs = super().create(vals_list)
		configs._xp_ensure_provider()
		return configs

	def _xp_ensure_provider(self):
		"""Każda konfiguracja ma swój rekord communication.provider."""
		Provider = self.env["communication.provider"].sudo()
		for config in self:
			if config.provider_id:
				continue
			Provider.create({
				"name": config.name,
				"code": f"xet_products_{config.code}",
				"company_id": config.company_id.id,
				"provider_type": "xet_products",
				"provider_config_id": config.id,
				"trigger_type": "cron_scheduled",
			})

	def _xp_sftp_params(self):
		self.ensure_one()
		config = self.sudo()
		return {
			"host": config.sftp_host,
			"port": config.sftp_port,
			"username": config.sftp_username,
			"password": config.sftp_password,
			"private_key": config.sftp_private_key,
			"passphrase": config.sftp_key_passphrase,
			"host_fingerprint": config.sftp_host_fingerprint,
			"timeout": config.sftp_timeout,
		}

	def _xp_sftp(self):
		return XpSftpClient(**self._xp_sftp_params())

	def _xp_active_run(self):
		self.ensure_one()
		return self.env["communication.log"].search([
			("xp_config_id", "=", self.id),
			("xp_result", "in", ACTIVE_RESULTS),
		], limit=1)

	# ------------------------------------------------------------------
	# Akcje
	# ------------------------------------------------------------------
	def action_test_connection(self):
		self.ensure_one()
		directory = self.sftp_in_dir if self.direction == "import" else self.sftp_out_dir
		try:
			with self._xp_sftp() as client:
				fingerprint = client.server_fingerprint
				files = client.list_files(directory, self.sftp_file_pattern if self.direction == "import" else "*")
		except Exception as exc:
			raise UserError(_("Test połączenia nieudany: %s") % exc) from exc
		message = _(
			"Połączenie poprawne.\nOdcisk klucza hosta: %(fp)s\nKatalog '%(dir)s': %(n)s plików.",
			fp=fingerprint, dir=directory or ".", n=len(files),
		)
		return {
			"type": "ir.actions.client",
			"tag": "display_notification",
			"params": {"title": _("SFTP"), "message": message, "sticky": True, "type": "success"},
		}

	def action_run_now(self):
		"""Tworzy przebieg i od razu uruchamia dyspozytora dla tego przebiegu."""
		self.ensure_one()
		log = self._xp_create_run()
		if not log:
			raise UserError(_("Dla tej konfiguracji trwa już przebieg."))
		self.env.cr.commit()
		self.env["communication.log"]._cron_xp_process(log_ids=log.ids)
		return self.action_open_logs()

	def action_open_logs(self):
		self.ensure_one()
		return {
			"type": "ir.actions.act_window",
			"name": _("Przebiegi: %s", self.name),
			"res_model": "communication.log",
			"view_mode": "list,form",
			"domain": [("xp_config_id", "=", self.id)],
		}

	# ------------------------------------------------------------------
	# Przebiegi
	# ------------------------------------------------------------------
	def _xp_create_run(self):
		"""Tworzy communication.log nowego przebiegu albo zwraca pusty
		recordset, gdy przebieg tej konfiguracji już trwa (wyłączność)."""
		self.ensure_one()
		if self._xp_active_run():
			return self.env["communication.log"]
		self._xp_ensure_provider()
		first_stage = "fetch" if self.direction == "import" else "select"
		Log = self.env["communication.log"]
		try:
			with self.env.cr.savepoint():
				log = Log.create({
					"provider_id": self.provider_id.id,
					"company_id": self.company_id.id,
					"direction": self.direction,
					"operation": "fetch" if self.direction == "import" else "send",
					"state": "draft",
					"template_id": self.template_id.id if self.direction == "export" else False,
					"xp_config_id": self.id,
					"xp_stage": first_stage,
					"xp_result": "pending",
				})
		except Exception as exc:  # unikalny indeks: równoległe utworzenie
			_logger.info("[XP] Przebieg dla konfiguracji %s nie utworzony: %s", self.id, exc)
			return Log
		_logger.info("[XP] Utworzono przebieg log_id=%s (%s, konfiguracja %s).", log.id, self.direction, self.code)
		return log

	@api.model
	def _cron_xp_init(self, direction=None):
		"""Cron inicjujący: zakłada przebieg dla każdej aktywnej konfiguracji
		bez trwającego przebiegu. Nie wykonuje żadnej pracy na danych."""
		domain = [("active", "=", True)]
		if direction:
			domain.append(("direction", "=", direction))
		for config in self.search(domain):
			if config.provider_id and not config.provider_id.active:
				continue
			config._xp_create_run()
		return True

	# ------------------------------------------------------------------
	# Kontrakt providera XET
	# ------------------------------------------------------------------
	def authenticate(self, **kwargs):
		self.ensure_one()
		with self._xp_sftp():
			return True

	def provider_test(self):
		return self.action_test_connection()

	def send_document(self, log):
		"""Ręczne 'Wyślij' z formularza logu - deleguje do dyspozytora."""
		self.env["communication.log"]._cron_xp_process(log_ids=log.ids)
		return True

	def fetch_documents(self, log=None):
		self.ensure_one()
		return self._xp_create_run()

	def get_status(self, log):
		return log.state
#EoF
