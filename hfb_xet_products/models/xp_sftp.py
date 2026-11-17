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


	Warstwa SFTP (paramiko) - bez ORM.

	Konfiguracja połączenia jak w hfb_apds (host, port, użytkownik, hasło
	albo klucz prywatny), uzupełniona o:
	  - klucze RSA / Ed25519 / ECDSA (hfb_apds obsługuje tylko RSA),
	  - opcjonalną weryfikację odcisku klucza hosta (SHA256, format ssh-keygen),
	  - atomową wysyłkę: zapis do pliku tymczasowego `.part` i rename.
"""
import base64
import fnmatch
import hashlib
import io
import logging
import posixpath
import stat

import paramiko

_logger = logging.getLogger(__name__)


class XpSftpError(Exception):
	pass


def host_key_fingerprint(key):
	"""Odcisk klucza hosta w formacie `SHA256:...` (jak ssh-keygen -l)."""
	digest = hashlib.sha256(key.asbytes()).digest()
	return "SHA256:" + base64.b64encode(digest).decode().rstrip("=")


def _load_private_key(text, passphrase=None):
	errors = []
	for key_cls in (paramiko.Ed25519Key, paramiko.ECDSAKey, paramiko.RSAKey):
		try:
			return key_cls.from_private_key(io.StringIO(text), password=passphrase or None)
		except Exception as exc:  # noqa: BLE001 - próbujemy kolejnych typów
			errors.append(f"{key_cls.__name__}: {exc}")
	raise XpSftpError("Nie udało się wczytać klucza prywatnego (" + "; ".join(errors) + ")")


class XpSftpClient:
	"""Kontekstowy klient SFTP: `with XpSftpClient(**params) as sftp: ...`"""

	def __init__(self, host, port=22, username=None, password=None,
				 private_key=None, passphrase=None, host_fingerprint=None,
				 timeout=30):
		self.host = host
		self.port = int(port or 22)
		self.username = username
		self.password = password
		self.private_key = private_key
		self.passphrase = passphrase
		self.host_fingerprint = (host_fingerprint or "").strip()
		self.timeout = timeout or 30
		self._transport = None
		self.sftp = None

	# --------------------------------------------------------------
	def __enter__(self):
		self.connect()
		return self

	def __exit__(self, *exc):
		self.close()
		return False

	def connect(self):
		if not self.host:
			raise XpSftpError("Brak hosta SFTP w konfiguracji.")
		sock = paramiko.Transport((self.host, self.port))
		sock.banner_timeout = self.timeout
		sock.auth_timeout = self.timeout
		try:
			sock.start_client(timeout=self.timeout)
			remote_key = sock.get_remote_server_key()
			actual = host_key_fingerprint(remote_key)
			if self.host_fingerprint and self.host_fingerprint != actual:
				raise XpSftpError(
					f"Odcisk klucza hosta {self.host} nie zgadza się: "
					f"oczekiwano {self.host_fingerprint}, otrzymano {actual}."
				)
			if self.private_key:
				sock.auth_publickey(self.username, _load_private_key(self.private_key, self.passphrase))
			else:
				sock.auth_password(self.username, self.password or "")
			if not sock.is_authenticated():
				raise XpSftpError("Uwierzytelnienie SFTP nie powiodło się.")
			self._transport = sock
			self.sftp = paramiko.SFTPClient.from_transport(sock)
			self.sftp.get_channel().settimeout(self.timeout)
			self.server_fingerprint = actual
		except Exception:
			sock.close()
			raise

	def close(self):
		try:
			if self.sftp:
				self.sftp.close()
		finally:
			if self._transport:
				self._transport.close()
			self.sftp = None
			self._transport = None

	# --------------------------------------------------------------
	def list_files(self, directory, pattern="*"):
		"""Lista plików (bez katalogów) pasujących do wzorca, od najstarszego."""
		entries = []
		for attr in self.sftp.listdir_attr(directory or "."):
			if stat.S_ISDIR(attr.st_mode or 0):
				continue
			if pattern and not fnmatch.fnmatch(attr.filename, pattern):
				continue
			entries.append(attr)
		entries.sort(key=lambda a: (a.st_mtime or 0, a.filename))
		return entries

	def download(self, remote_path, local_path):
		remote_size = self.sftp.stat(remote_path).st_size
		self.sftp.get(remote_path, local_path)
		return remote_size

	def upload_atomic(self, local_path, remote_dir, remote_name):
		"""Zapis do `<nazwa>.part`, potem rename na nazwę docelową."""
		target = posixpath.join(remote_dir or ".", remote_name)
		tmp = target + ".part"
		self.sftp.put(local_path, tmp, confirm=True)
		try:
			self.sftp.posix_rename(tmp, target)
		except (IOError, paramiko.SFTPError):
			# serwer bez rozszerzenia posix-rename
			try:
				self.sftp.remove(target)
			except IOError:
				pass
			self.sftp.rename(tmp, target)
		return target, self.sftp.stat(target).st_size

	def move(self, remote_path, target_dir):
		"""Przeniesienie pliku do katalogu (np. archive/error).
		Przy kolizji nazwy dokleja znacznik czasu modyfikacji."""
		name = posixpath.basename(remote_path)
		target = posixpath.join(target_dir, name)
		try:
			self.sftp.stat(target)
			base, ext = posixpath.splitext(name)
			mtime = int(self.sftp.stat(remote_path).st_mtime or 0)
			target = posixpath.join(target_dir, f"{base}.{mtime}{ext}")
		except IOError:
			pass
		self.sftp.rename(remote_path, target)
		return target
#EoF
