# hfb_xet_products – uwagi i rozbieżności do dalszych prac

Stan na 16.11.2026.

**Źródła**
- kod modułu w wersji 20.0.0.2.1 (`hfb_xet_products.zip` z 16.11.2026),
- XET Base `hfb_xmlmap_exporter`, gałąź `20.0`, commit `38223ca` (GitHub `Hadron4biz/hfb_xmlmap_exporter`),
- kod Odoo 20.0 Community (`odoo/addons/base/models/ir_module.py`, `odoo/tools/mail.py`, `odoo/http/router.py`, `odoo/tools/image.py`, `html_editor`, `web`),
- testy lokalne na czystym Odoo 20 z plikiem `45ca7c3c-794.xml` (1015 produktów) i lokalnym serwerem SFTP/HTTP,
- przebiegi z instancji `adler_dev` zgłoszone przez użytkownika (log i zrzuty ekranu z 16.11.2026). Konektor do instancji nie był dostępny; danych instancji nie odczytywano bezpośrednio.

Ustalenia z rozmowy (decyzje użytkownika): XET Base bez zmian; cała logika w module; jeden worker; klucz SKU + EAN; plik importu nie jest pomijany, nawet jeśli był wczytany; zdjęcie główne pobierane do `image_1920`; zaokrąglenie wagi brutto i sanitizacja opisów HTML pozostają bez zmian; sygnalizowanie produktów wycofanych w eksporcie przyrostowym poza zakresem.

Numery linii odnoszą się do plików z archiwum `hfb_xet_products.zip` w wersji 20.0.0.2.1.

**Oznaczenia**
- **[kod]** – fakt wynikający bezpośrednio z kodu modułu,
- **[dane]** – fakt z testu lokalnego albo z przebiegu zgłoszonego przez użytkownika (źródło podane),
- **[hipoteza]** – przypuszczenie do sprawdzenia; nie zostało zweryfikowane.

---

## Przebiegi i przetwarzanie (ST)

### ST-01 · Zdublowane wpisy w chatterze przebiegu · [kod] [dane]
- **Gdzie:** `models/communication_log.py` l. 72 i 83 (`tracking=True` dla `xp_stage`, `xp_result`); `models/communication_log_import.py` l. 520 (`"message": summary`) i l. 522 (`self._xp_post(summary)`); XET Base `models/communication_log.py` l. 70 (`message` z `tracking=True`).
- **Stan:** każda zmiana etapu i wyniku daje osobny wpis śledzenia; podsumowanie przebiegu pojawia się dwa razy (śledzenie pola „Opis zdarzenia” i wpis `message_post`).
- **Dane adler_dev:** widoczne na zrzucie przebiegu log 5 (16.11.2026).
- **Dane adler_dev (17.11.2026, wersja 20.0.0.3.0):** dubel widoczny także w przebiegach log 9–11 (podsumowanie importu), w przebiegu „brak pliku” (komunikat „Brak plików pasujących do wzorca…”) i w log 13 (komunikat błędu etapu parse).

### ST-02 · „Uruchom przebieg teraz” wykonuje etapy w żądaniu HTTP · [kod] [hipoteza]
- **Gdzie:** `models/xp_config.py` l. 233 (`_cron_xp_process(log_ids=log.ids)` w `action_run_now`); `models/communication_log.py` l. 46 (`DEFAULT_BUDGET_SECONDS = 240`).
- **Stan:** dyspozytor działa w żądaniu przycisku z budżetem 240 s (parametr `hfb_xet_products.cron_budget_seconds` zmienia tylko budżet).
- **[hipoteza]** przy `limit_time_real` serwera mniejszym niż budżet (domyślnie w Odoo 120 s) żądanie może zostać przerwane w trakcie etapu; etap zostaje wtedy w stanie `running` do odzyskania po `lock_timeout_minutes`.

### ST-03 · Odzyskiwanie etapu wyłącznie po czasie blokady · [kod]
- **Gdzie:** `models/communication_log.py` l. 239 (`timeout = timedelta(minutes=...lock_timeout_minutes...)`).
- **Stan:** etap `running` starszy niż `lock_timeout_minutes` (domyślnie 30) jest uznawany za przerwany, bez sprawdzenia, czy proces nadal działa. Etapy `apply` i `images` mają limit 60 s na krok (`communication_log_import.py` l. 44–45); `parse` (l. 140), `generate` i `send` (`communication_log_export.py` l. 98, 135) wykonują się w jednym kroku bez limitu.

### ST-04 · Pamięć podręczna typów mediów po błędzie linii · [kod] [hipoteza]
- **Gdzie:** `models/communication_log_import.py` l. 215 (`caches["categ"].clear()` po wyjątku linii), l. 331 (tworzenie brakującego `xp.media.type`).
- **Stan:** po wyjątku linii czyszczona jest tylko pamięć kategorii. Typ medium utworzony w savepoincie wycofanej linii pozostaje w `caches["media_type"]`.
- **[hipoteza]** kolejne linie z tym typem w tym samym kroku dostaną błąd klucza obcego. Dotyczy tylko typów spoza danych modułu (6 typów jest w `data/xp_media_type_data.xml`).

### ST-05 · Wyszukiwanie produktu po kluczu bez filtra firmy · [kod]
- **Gdzie:** `models/communication_log_import.py` l. 252 (`("default_code", "=", data["sku"])`, `("barcode", "=", ...)`).
- **Stan:** domena nie zawiera `company_id`; przy wielu firmach może trafić produkt innej firmy. Nowe produkty są tworzone bez `company_id`.

### ST-06 · Przyrost danych przebiegów · [kod] [dane]
- **Gdzie:** `models/communication_log_import.py` l. 112 (`"file_data": ...` – pełny plik w każdym przebiegu); modele `communication.log` i `xp.staging.line` bez mechanizmu czyszczenia.
- **Stan:** każdy przebieg importu zapisuje kopię pliku i jedną linię stagingu na produkt. Bez katalogu archiwum każdy przebieg importuje plik ponownie (ST-10).
- **Dane:** plik testowy 8,3 MB, 1015 linii na przebieg; przy cronie importu co 1 h – 24 kopie pliku na dobę.

### ST-07 · Eksport bez produktów ustawia `state = draft` · [kod]
- **Gdzie:** `models/communication_log_export.py` l. 86.
- **Stan:** przebieg z wynikiem `nofile` pozostaje w bazowym stanie XET `draft`.

### ST-08 · Zakres eksportu przyrostowego · [kod] [hipoteza]
- **Gdzie:** `models/communication_log_export.py` l. 70 (`("write_date", ">", previous.xp_cutoff)` oraz `xp_media_ids.write_date`).
- **Stan:** delta obejmuje zmiany `write_date` szablonu produktu i jego mediów.
- **[hipoteza]** zmiany zapisane wyłącznie na `product.product`, w nazwie kategorii albo w podatku nie zmieniają `write_date` szablonu i nie trafią do delty.

### ST-09 · Pobranie pliku w trakcie zapisu przez wystawcę · [kod]
- **Gdzie:** `models/communication_log_import.py` l. 95 (`client.download`), kontrola rozmiaru l. 97–99.
- **Stan:** porównywany jest rozmiar przed i po pobraniu; brak warunku minimalnego wieku pliku. Wystawca publikuje plik pod stałą nazwą (informacja od użytkownika).

### ST-10 · Wybór pliku bez katalogu archiwum · [kod]
- **Gdzie:** `models/communication_log_import.py` l. 91 (`entries[0] if config.sftp_archive_dir else entries[-1]`).
- **Stan:** bez archiwum wczytywany jest zawsze najnowszy plik; starsze pliki w katalogu wejściowym nie są importowane.

### ST-11 · Przebieg bez szablonu przy błędzie parsowania · [kod] [dane]
- **Gdzie (20.0.0.3.0):** `models/communication_log_import.py`, `_xp_stage_parse` – `template_id` zapisywany na przebiegu dopiero po przeczytaniu wszystkich rekordów (`self.write({... "template_id": template.id})`); błąd korzenia lub składni XML przerywa etap wcześniej.
- **Stan:** przy błędzie parsowania pole „Szablon” przebiegu jest puste; nie widać, którym szablonem próbowano czytać plik.
- **Dane adler_dev:** log 13 (17.11.2026), plik `test-0001.xml`.

### ST-12 · Komunikat błędu z nazwą pliku roboczego · [kod] [dane]
- **Gdzie (20.0.0.3.0):** `models/communication_log_import.py`, `_xp_stage_fetch` (plik lokalny `f"{self.id}_{attr.filename}"`) i `_xp_stage_parse` (`XpFatalError(f"Niepoprawny plik XML: {exc}")` – tekst wyjątku lxml zawiera ścieżkę lokalną).
- **Stan:** komunikat podaje nazwę pliku roboczego z prefiksem id przebiegu, a nie nazwę zdalną.
- **Dane adler_dev:** log 13 – „…line 1, column 1 (13_test-0001.xml, line 1)” zamiast `test-0001.xml`.

### ST-13 · Zakładka „Dane techniczne KSeF” w przebiegach XET Products · [dane]
- **Gdzie:** widok formularza `communication.log` z XET Base.
- **Stan:** zakładka z polami KSeF (Session ID, Access Token, HTTP Status itd.) jest widoczna i pusta w przebiegach XET Products.
- **Dane adler_dev:** log 11 (17.11.2026).

---

## Komunikacja SFTP i HTTP (SFTP)

### SFTP-01 · Weryfikacja klucza hosta opcjonalna · [kod]
- **Gdzie:** `models/xp_sftp.py` l. 80 (`if self.host_fingerprint and ...`).
- **Stan:** pusty odcisk w konfiguracji oznacza akceptację dowolnego klucza serwera.

### SFTP-02 · Wysyłka bez `posix-rename` nie jest atomowa · [kod]
- **Gdzie:** `models/xp_sftp.py` l. 137 (`self.sftp.remove(target)` przed `rename`).
- **Stan:** gdy serwer nie obsługuje rozszerzenia `posix-rename`, plik docelowy jest usuwany, a potem zastępowany; między operacjami pliku nie ma.

### SFTP-03 · Wzorzec nazwy rozróżnia wielkość liter · [kod]
- **Gdzie:** `models/xp_sftp.py` l. 116 (`fnmatch.fnmatch`).
- **Stan:** wzorzec `*.xml` nie obejmuje `*.XML`.

---

## Bezpieczeństwo i uprawnienia (SEC)

### SEC-01 · Hasło SFTP przechowywane jako tekst · [kod]
- **Gdzie:** `models/xp_config.py` l. 59 (`sftp_password = fields.Char(..., groups="base.group_system")`).
- **Stan:** wartość w bazie bez szyfrowania; odczyt w interfejsie ograniczony do grupy Ustawienia (jak w `hfb_apds`).

### SEC-02 · Media produktu edytowalne przez każdego użytkownika wewnętrznego · [kod]
- **Gdzie:** `security/ir.access.csv` l. 7 (`xp.product.media`, `base.group_user`, `crud`).

### SEC-03 · Linie importu dostępne tylko dla grupy Ustawienia · [kod] [hipoteza]
- **Gdzie:** `security/ir.access.csv` l. 4; `views/communication_log_views.xml` l. 37 (`xp_staging_ids` w formularzu logu).
- **[hipoteza]** użytkownik spoza `base.group_system` z dostępem do Logów komunikacji dostanie błąd dostępu przy otwarciu przebiegu importu.

### SEC-04 · Pobieranie zdjęć z adresów z pliku · [kod]
- **Gdzie:** `models/communication_log_import.py` l. 440 (sprawdzany tylko schemat `http`/`https`).
- **Stan:** serwer Odoo wykonuje żądania HTTP pod adresy podane w pliku importu, także adresy sieci wewnętrznej.

### SEC-05 · Dokumentacja w `static/description/doc/` jest publiczna · [kod Odoo]
- **Gdzie:** Odoo 20 `odoo/http/stream.py` (pliki `static/` serwowane z `public=True`).
- **Stan:** strony są dostępne bez logowania pod `/hfb_xet_products/static/description/doc/`. Zawierają nazwy modeli, pól, cronów, liczby rekordów z testu i z przebiegu adler_dev; nie zawierają hostów, użytkowników ani haseł.

---

## Zależność od XET Base (XET)

### XET-01 · Nadpisanie `_compute_provider_model` · [kod]
- **Gdzie:** `models/communication_provider.py` l. 27.
- **Stan:** XET Base 20.0 ma w tej metodzie słownik typów wpisany na sztywno; moduł zastępuje całą metodę. Zmiana tej metody w XET Base nie zostanie przejęta.

### XET-02 · Zmiana poziomu loggera XET na czas generowania · [kod]
- **Gdzie:** `models/communication_log_export.py` l. 38 (`logger.setLevel(level)`, poziom ERROR).
- **Stan:** poziom loggera `odoo.addons.hfb_xmlmap_exporter` jest zmieniany dla całego procesu, także dla innych wątków działających w tym czasie (np. KSeF).

### XET-03 · Porządkowanie XML poza XET Base · [kod]
- **Gdzie:** `models/xp_xml.py` (`clean_xet_xml`).
- **Stan:** usunięcie `xmlns`/`xmlns:xsi` i CDATA wg `as_cdata` wykonuje moduł, bo `generate_xml` w XET Base 20.0 tego nie robi.

### XET-04 · Kolejność produktów w eksporcie · [kod] [dane]
- **Gdzie:** `data/xet_template_products.xml` l. 36 (`loop_order` = `default_code`).
- **Dane (test lokalny):** kolejność w pliku źródłowym nie jest posortowana po SKU; eksport jest.

---

## Mapowanie danych (MAP)

### MAP-01 · Zaokrąglenie wagi brutto · [kod] [dane] – decyzja: bez zmian
- **Gdzie:** `models/xp_parser.py` l. 72 (`Product_gross_weight` → `weight`).
- **Stan:** `weight` ma precyzję „Stock Weight” (2 miejsca).
- **Dane (test lokalny):** 439 rekordów różni się w eksporcie (np. 0.877 → 0.88).

### MAP-02 · Sanitizacja opisów HTML · [kod] [dane] – decyzja: bez zmian
- **Gdzie:** `models/xp_parser.py` l. 84–85 (pola Html `xp_description_short`, `description`).
- **Dane (test lokalny):** 279 (`Product_description`) i 258 (`Product_preliminary_description`) rekordów różni się formą: owinięcie w `<p>`, `&` → `&amp;`, encje → znaki.

### MAP-03 · Pola tłumaczone zapisywane w jednym języku · [kod]
- **Gdzie:** `models/communication_log_import.py` l. 225 (`lang=config.xp_lang`).
- **Stan:** import zapisuje nazwę i opisy w języku kanału (domyślnie `pl_PL`).

### MAP-04 · Jeden SKU = jeden szablon produktu · [kod]
- **Stan:** brak grupowania wariantów (np. kolorów po sufiksie SKU).

---

## Elementy niedokończone lub ograniczone (DEAD)

### DEAD-01 · Walidacja XSD bez XSD w dostarczonym szablonie · [kod]
- **Gdzie:** `models/xp_config.py` l. 121 (`export_validate_xsd`, domyślnie włączone).
- **Stan:** działa tylko, gdy szablon ma podpięty plik XSD; szablon `xet_template_products` go nie ma.

### DEAD-02 · Kontrakt providera XET w minimalnej postaci · [kod]
- **Gdzie:** `models/xp_config.py` l. 293–311.
- **Stan:** `authenticate` tylko otwiera połączenie SFTP, `fetch_documents` tworzy przebieg, `get_status` zwraca `state` logu, `send_document` uruchamia dyspozytora dla logu.

---

## Obserwacje z danych

| Źródło | Data | Obserwacja |
|---|---|---|
| test lokalny | 16.11.2026 | import 1015 produktów ok. 86 s; ponowny import tego samego pliku: 1015 bez zmian |
| test lokalny | 16.11.2026 | eksport 1015 produktów ok. 20 s; zgodność struktury z plikiem źródłowym (56 pól, CDATA w 10 polach) |
| test lokalny | 16.11.2026 | pole Image Odoo 20 zapisuje treść zaczynającą się od `<` bez weryfikacji (traktowana jak SVG); moduł sprawdza obraz przez PIL |
| adler_dev (zgłoszone) | 16.11.2026 | log 5: 1166 rekordów, utworzone 80, zaktualizowane 3, bez zmian 1083, błędy 0; zdjęcia pobrane 1166, błędy 0 |
| adler_dev (zgłoszone) | 16.11.2026 | serwer odoo20 pobiera zdjęcia z adresów ADLER (0 błędów pobrania) |

---

## Do weryfikacji na instancji

1. Przyczyna 3 aktualizacji w przebiegu log 5 (lista linii z wynikiem „Zaktualizowano” w zakładce XET Products).
2. `limit_time_real` serwera wobec budżetu 240 s przy „Uruchom przebieg teraz” (ST-02).
3. Czy wystawca nadpisuje plik w miejscu (ST-09) i czy na serwerze ustawiony jest katalog archiwum (ST-06, ST-10).
4. Dostęp do przebiegu importu dla użytkownika spoza grupy Ustawienia (SEC-03).
5. Konfiguracja i interwały trzech cronów po aktywacji.
6. Odcisk klucza hosta wpisany w kanałach (SFTP-01).

---

## Dokumentacja – stan i otwarte punkty

| Element | Stan |
|---|---|
| Strony UML (8) w `static/description/doc/` | gotowe, język polski |
| Diagramy BPMN (3) `.bpmn` | gotowe, poza modułem – w pakiecie dokumentacji |
| `README.md` | gotowy, angielski |
| `static/description/index.html` | gotowy, angielski, zgodny z sanitizerem (test `sanitize_check`) |
| Diagram komponentów | szeroki; czytelny w trybie „Rozmiar rzeczywisty” |
