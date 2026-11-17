# -*- coding: utf-8 -*-
#################################################################################
#
# Odoo, Open ERP Source Management Solution
# Copyright (C) 2026 Hadron for business sp. z o.o.
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as
# published by the Free Software Foundation, either version 3 of the
# License, or (at your option) any later version.
#
#################################################################################
# STATUS: prototyp.
#
# Moduł rozszerza XET Base (`hfb_xmlmap_exporter`) o provider `xet_products`:
#   - import produktów z pliku XML (SFTP) - dedykowany parser w module,
#   - cykliczny eksport produktów do XML wg szablonu XET i wysyłka SFTP.
#
# XET Base NIE jest modyfikowany. Moduł korzysta wyłącznie z jego
# mechanizmów (communication.provider, communication.log,
# xml.export.template.generate_xml).
#
# Przebieg: jeden rekord `communication.log` = jeden przebieg.
#   Import:  fetch -> parse -> apply -> images -> finalize
#   Eksport: select -> generate -> send -> finalize
# Jeden worker (jeden cron dyspozytora), wyłączność przebiegu na konfigurację.
#################################################################################
{
	"name": "XET Products (import/eksport produktów XML)",
	"version": "20.0.0.2.1",
	"category": "Inventory/Products",
	"summary": "Import i eksport produktów XML przez SFTP na bazie XET Base",
	"author": "Hadron for business sp. z o.o.",
	"website": "https://hadron.eu.com",
	"license": "AGPL-3",
	"depends": [
		"product",
		"account",
		"hfb_xmlmap_exporter",
	],
	"external_dependencies": {
		"python": ["paramiko", "lxml", "requests"],
	},
	"data": [
		"security/ir.access.csv",
		"data/xp_media_type_data.xml",
		"data/ir_cron.xml",
		"views/xp_config_views.xml",
		"views/communication_log_views.xml",
		"views/product_template_views.xml",
		"views/xp_media_views.xml",
		"views/menu.xml",
		"data/xet_template_products.xml",
	],
	"installable": True,
	"application": False,
	"auto_install": False,
}
