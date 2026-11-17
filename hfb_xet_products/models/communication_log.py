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


	Przebiegi XET Products w communication.log.

	`communication.log` pozostaje centralną osią przebiegu (jak w hfb_apds):
	jeden rekord = jeden przebieg importu albo eksportu. Moduł dodaje własną
	warstwę pól `xp_*`, nie zmieniając znaczenia bazowych `state`, `status`
	i `operation` z XET Base.

	Etapy:
		import:  fetch -> parse -> apply -> images -> finalize
		eksport: select -> generate -> send -> finalize

	WYŁĄCZNOŚĆ
		1. Domena: dyspozytor bierze wyłącznie logi z `xp_config_id`
		   (crony KSeF w XET Base filtrują po provider_type='ksef').
		2. Przebieg: najwyżej jeden aktywny przebieg (pending/running) na
		   konfigurację - unikalny indeks częściowy w bazie.
		3. Etap: atomowe przejście pending -> running (UPDATE ... WHERE),
		   z zapisem xp_lock_at.
		4. Kierunki: eksport nie startuje, gdy w firmie trwa import.
		5. Etap 'running' dłużej niż limit z konfiguracji jest uznawany
		   za przerwany i ponawiany (do max_attempts), potem 'error'.

	Jeden worker: jeden cron dyspozytora (`_cron_xp_process`) z budżetem
	czasu; etap apply pracuje partiami i kontynuuje w kolejnym wywołaniu.
"""
import logging
import time
import traceback
from datetime import timedelta

from markupsafe import Markup, escape

from odoo import fields, models

_logger = logging.getLogger(__name__)

STAGES = {
	"import": ["fetch", "parse", "apply", "images", "finalize"],
	"export": ["select", "generate", "send", "finalize"],
}
STAGE_AGAIN = "again"	# etap niedokończony (np. kolejna partia) - zostaje
STAGE_STOP = "stop"		# przebieg zakończony wcześniej (wynik ustawiony w etapie)

DEFAULT_BUDGET_SECONDS = 240


class XpFatalError(Exception):
	"""Błąd, którego ponowienie nic nie zmieni (np. niepoprawny plik)."""


class CommunicationLog(models.Model):
	_inherit = "communication.log"

	xp_config_id = fields.Many2one(
		"communication.provider.xet_products", string="Konfiguracja XET Products",
		index=True, ondelete="restrict", readonly=True,
	)
	xp_direction = fields.Selection(related="xp_config_id.direction", string="Kierunek XP")
	xp_stage = fields.Selection(
		[
			("fetch", "Import 1 - pobranie pliku"),
			("parse", "Import 2 - parsowanie"),
			("apply", "Import 3 - zapis produktów"),
			("images", "Import 4 - zdjęcia główne"),
			("select", "Eksport 1 - wybór produktów"),
			("generate", "Eksport 2 - generowanie XML"),
			("send", "Eksport 3 - wysyłka"),
			("finalize", "Zakończenie"),
		],
		string="Etap", index=True, tracking=True, readonly=True,
	)
	xp_result = fields.Selection(
		[
			("pending", "Oczekuje"),
			("running", "W trakcie"),
			("accepted", "Zakończony poprawnie"),
			("manual", "Zakończony z błędami - do przejrzenia"),
			("nofile", "Brak danych do przetworzenia"),
			("error", "Błąd"),
		],
		string="Wynik", index=True, tracking=True, readonly=True,
	)
	xp_lock_at = fields.Datetime(string="Start etapu", readonly=True)
	xp_attempts = fields.Integer(string="Próby etapu", readonly=True)
	xp_next_try_at = fields.Datetime(string="Następna próba", readonly=True)
	xp_last_error = fields.Text(string="Ostatni błąd", readonly=True)

	# Import
	xp_remote_path = fields.Char(string="Plik zdalny", readonly=True)
	xp_local_path = fields.Char(string="Plik roboczy", readonly=True)
	xp_source_signature = fields.Char(
		string="Sygnatura pliku", index=True, readonly=True,
		help="nazwa|rozmiar|mtime - chroni przed ponownym importem tego "
			 "samego pliku, gdy plik zostaje w katalogu wejściowym.",
	)
	xp_staging_ids = fields.One2many("xp.staging.line", "log_id", string="Linie importu")

	# Eksport
	xp_cutoff = fields.Datetime(
		string="Stan danych na", readonly=True,
		help="Moment wyboru produktów. Dla eksportu przyrostowego kolejny "
			 "przebieg wybiera zmiany późniejsze niż ta wartość z ostatniego "
			 "poprawnego przebiegu.",
	)
	xp_product_ids = fields.Many2many(
		"product.template", "xp_log_product_rel", "log_id", "product_tmpl_id",
		string="Produkty w eksporcie", readonly=True,
	)

	# Liczniki
	xp_count_total = fields.Integer(string="Rekordy", readonly=True)
	xp_count_created = fields.Integer(string="Utworzone", readonly=True)
	xp_count_updated = fields.Integer(string="Zaktualizowane", readonly=True)
	xp_count_unchanged = fields.Integer(string="Bez zmian", readonly=True)
	xp_count_error = fields.Integer(string="Błędy", readonly=True)
	xp_count_images = fields.Integer(string="Pobrane zdjęcia", readonly=True)
	xp_count_image_errors = fields.Integer(string="Błędy zdjęć", readonly=True)

	_xp_one_active_run = models.UniqueIndex(
		"(xp_config_id) WHERE xp_config_id IS NOT NULL AND xp_result IN ('pending', 'running')",
		"Dla tej konfiguracji trwa już przebieg XET Products.",
	)

	# ------------------------------------------------------------------
	# Wyłączność etapu
	# ------------------------------------------------------------------
	def _xp_try_acquire(self):
		"""pending -> running atomowo. True = ten proces wykonuje etap."""
		self.ensure_one()
		self.env.cr.execute(
			"""
			UPDATE communication_log
			   SET xp_result = 'running', xp_lock_at = now() AT TIME ZONE 'UTC',
				   state = CASE WHEN state = 'draft' THEN 'queued' ELSE state END
			 WHERE id = %s AND xp_result = 'pending'
			""",
			(self.id,),
		)
		acquired = self.env.cr.rowcount == 1
		self.env.cr.commit()
		self.invalidate_recordset()
		return acquired

	def _xp_next_stage(self):
		order = STAGES[self.xp_config_id.direction]
		idx = order.index(self.xp_stage)
		return order[idx + 1] if idx + 1 < len(order) else None

	def _xp_post(self, text, error=False):
		body = escape(text).replace("\n", Markup("<br/>"))
		if error:
			body = Markup("<b>⚠️</b> ") + body
		self.message_post(body=body)

	# ------------------------------------------------------------------
	# Wykonanie jednego kroku
	# ------------------------------------------------------------------
	def _xp_run_step(self):
		"""Wykonuje bieżący etap przebiegu. Zwraca True, jeśli przebieg
		może być kontynuowany od razu (kolejny etap w tym samym wywołaniu)."""
		self.ensure_one()
		if not self._xp_try_acquire():
			return False
		stage = self.xp_stage
		started = time.monotonic()
		_logger.info("[XP] log_id=%s: etap %s - start.", self.id, stage)
		try:
			outcome = getattr(self, f"_xp_stage_{stage}")()
		except Exception as exc:  # noqa: BLE001 - każdy błąd etapu kończy krok
			self.env.cr.rollback()
			self.env.invalidate_all()
			_logger.exception("[XP] log_id=%s: etap %s - błąd.", self.id, stage)
			self._xp_fail_step(exc, traceback.format_exc(), fatal=isinstance(exc, XpFatalError))
			return False

		duration = int((time.monotonic() - started) * 1000)
		vals = {"xp_attempts": 0, "xp_next_try_at": False, "duration_ms": duration}
		if outcome == STAGE_AGAIN:
			vals["xp_result"] = "pending"
		elif outcome == STAGE_STOP or stage == "finalize":
			pass	# wynik końcowy ustawił etap
		else:
			next_stage = self._xp_next_stage()
			vals.update({"xp_stage": next_stage, "xp_result": "pending"})
		self.write(vals)
		self.env.cr.commit()
		_logger.info("[XP] log_id=%s: etap %s - koniec (%s, %s ms).", self.id, stage, outcome or "ok", duration)
		return self.xp_result == "pending"

	def _xp_fail_step(self, exc, tb=None, fatal=False):
		config = self.xp_config_id
		attempts = self.xp_attempts + 1
		message = f"Etap {self.xp_stage}: {exc}"
		if fatal or attempts >= max(config.max_attempts, 1):
			self.write({
				"xp_attempts": attempts,
				"xp_result": "error",
				"xp_last_error": tb or message,
				"state": "error",
				"status": "error",
				"message": message,
			})
			self._xp_post(f"{message}\nPrzebieg przerwany (próby: {attempts}).", error=True)
			self.env.cr.commit()
			try:
				self._xp_on_run_error()
				self.env.cr.commit()
			except Exception:  # noqa: BLE001 - sprzątanie best-effort
				self.env.cr.rollback()
				_logger.exception("[XP] log_id=%s: sprzątanie po błędzie nieudane.", self.id)
		else:
			delay = timedelta(minutes=max(config.retry_delay_minutes, 0))
			self.write({
				"xp_attempts": attempts,
				"xp_result": "pending",
				"xp_next_try_at": fields.Datetime.now() + delay,
				"xp_last_error": tb or message,
			})
			self._xp_post(f"{message}\nPonowienie {attempts + 1}/{config.max_attempts} po {delay}.", error=True)
			self.env.cr.commit()

	def _xp_stage_finalize(self):
		"""Rozszerzane w communication_log_import/export (wg kierunku)."""
		raise NotImplementedError(f"Brak etapu finalize dla kierunku {self.xp_config_id.direction!r}.")

	def _xp_on_run_error(self):
		"""Haczyk sprzątający po definitywnym błędzie przebiegu
		(rozszerzany w modułach import/eksport)."""
		return True

	# ------------------------------------------------------------------
	# Odzyskiwanie przerwanych etapów
	# ------------------------------------------------------------------
	def _xp_recover_stale(self):
		now = fields.Datetime.now()
		running = self.search([("xp_config_id", "!=", False), ("xp_result", "=", "running")])
		for log in running:
			timeout = timedelta(minutes=max(log.xp_config_id.lock_timeout_minutes, 1))
			if log.xp_lock_at and log.xp_lock_at + timeout < now:
				_logger.warning("[XP] log_id=%s: etap %s przerwany (blokada od %s).", log.id, log.xp_stage, log.xp_lock_at)
				log._xp_fail_step(Exception(f"etap przerwany - blokada od {log.xp_lock_at} przekroczyła {timeout}"))

	# ------------------------------------------------------------------
	# Dyspozytor (cron)
	# ------------------------------------------------------------------
	def _xp_import_active(self, company):
		return bool(self.search_count([
			("xp_config_id.direction", "=", "import"),
			("company_id", "=", company.id),
			("xp_result", "in", ("pending", "running")),
			("xp_stage", "!=", "fetch"),
		], limit=1))

	def _cron_xp_process(self, log_ids=None, budget_seconds=None):
		"""Dyspozytor przebiegów XET Products (jeden worker)."""
		if budget_seconds is None:
			budget_seconds = self.env["ir.config_parameter"].sudo().get_int(
				"hfb_xet_products.cron_budget_seconds") or DEFAULT_BUDGET_SECONDS
		deadline = time.monotonic() + budget_seconds

		self._xp_recover_stale()

		now = fields.Datetime.now()
		domain = [
			("xp_config_id", "!=", False),
			("xp_result", "=", "pending"),
			"|", ("xp_next_try_at", "=", False), ("xp_next_try_at", "<=", now),
		]
		if log_ids:
			domain.append(("id", "in", list(log_ids)))
		# import przed eksportem, starsze przed nowszymi
		logs = self.search(domain).sorted(lambda l: (l.xp_config_id.direction != "import", l.id))

		for log in logs:
			while time.monotonic() < deadline:
				log.invalidate_recordset()
				if log.xp_result != "pending":
					break
				if log.xp_next_try_at and log.xp_next_try_at > fields.Datetime.now():
					break
				if (log.xp_config_id.direction == "export" and log.xp_stage == "select"
						and self._xp_import_active(log.company_id)):
					_logger.info("[XP] log_id=%s: eksport czeka na zakończenie importu.", log.id)
					break
				if not log._xp_run_step():
					break
			if time.monotonic() >= deadline:
				_logger.info("[XP] Budżet czasu dyspozytora wyczerpany - kontynuacja w kolejnym wywołaniu.")
				break
		return True
#EoF
