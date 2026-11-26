# EventTech Barcode Manager

Lokale Inventar- und Lagerverwaltungssoftware für Veranstaltungstechnik auf Windows und Raspberry Pi OS
64-Bit.

## Installation

Python 3.11 oder neuer wird für die Entwicklung benötigt.

```bat
C:\Python315\python.exe -m pip install -r requirements.txt
C:\Python315\python.exe main.py
```

## Artikeldetails

In der Artikelverwaltung öffnet **Details** alle gespeicherten Artikeldaten. Ein Doppelklick auf einen
Artikel öffnet dieselbe Ansicht. Neben Stammdaten werden, falls vorhanden, Barcodes, DMX-Daten,
Reparaturen, Dokumente, Fotos, Lagerbewegungen und Scan-Verlauf angezeigt.

Beim Erstellen oder Bearbeiten eines Artikels kann **Bedienhinweise automatisch ergänzen** aktiviert werden.
Die technischen Daten erhalten dann allgemeine, an der Kategorie orientierte Sicherheitshinweise sowie einen
Suchlink nach einer passenden Herstelleranleitung. Die Hinweise werden beim Speichern aktualisiert, nicht
mehrfach angehängt; beim Deaktivieren wird nur der automatisch erzeugte Abschnitt entfernt. Der Suchlink wird
erst beim Anklicken an Google übermittelt und muss anhand des exakten Modells geprüft werden.

Im selben Dialog öffnet **DMX-Modi und Kanäle bearbeiten** den DMX-Editor. Dort lassen sich mehrere Modi
mit jeweils bis zu 512 Kanälen anlegen, umbenennen und entfernen. Pro Kanal können Nummer, Funktion,
Wertebereich und Beschreibung gepflegt werden. Die Änderungen werden erst mit dem Artikel gespeichert;
vorhandene Modus- und Kanal-IDs bleiben beim Bearbeiten erhalten.

## Datenablage

- Datenbank: `%LOCALAPPDATA%\EventTechBarcodeManager\eventtech.db`
- Fotos und Dokumente: `%LOCALAPPDATA%\EventTechBarcodeManager\Dateien`
- Backups: `%LOCALAPPDATA%\EventTechBarcodeManager\Backups`

## Scanner

Die Scanner-Erfassung läuft über das Tastatur-HID-Protokoll. Zebra-USB- und Bluetooth-HID-Scanner werden
ohne spezielle Treiber unterstützt. Unter **Einstellungen → Scanner auswählen** lassen sich von Windows
gemeldete Tastatur-/HID-Geräte auflisten und ein bevorzugtes Gerät merken. HID-Tastaturscanner können von
Windows jedoch nicht immer einzeln von einer normalen Tastatur unterschieden werden. Präfix, Suffix und
Scanner-Test sind ebenfalls dort verfügbar.

## iPad oder Android als Scanner

1. Windows-PC und iPad/Android-Gerät mit demselben lokalen WLAN verbinden.
2. In **Einstellungen → iPad / Android** die Kopplung starten und den QR-Code im Mobilbrowser öffnen.
3. Auf der geöffneten Seite das **Kamera-Zertifikat installieren** laden. Auf dem iPad das geladene Profil unter **Einstellungen → Profil geladen** installieren und unter **Allgemein → Info → Zertifikatsvertrauenseinstellungen** vollständig aktivieren. Android kann je nach Version ebenfalls eine Installation des CA-Zertifikats verlangen.
4. Die angezeigte **HTTPS-Adresse** öffnen, **Live-Kamera starten** antippen und den Barcode mittig ins Bild halten. Erkannte Codes werden automatisch dekodiert und protokolliert.
5. Falls die Live-Kamera nicht genutzt werden kann, steht weiterhin die Fotoaufnahme zur Verfügung.

Die Kameraauswertung erfolgt lokal auf dem PC. Es wird keine externe Scan-Cloud verwendet. Für die Kopplung muss
Windows eingehende Verbindungen im privaten Netzwerk zulassen. Das lokale Zertifikat bleibt auf diesem PC; bei
einer geänderten Netzwerkadresse kann eine erneute Installation nötig sein. Der Kopplungs-QR-Code enthält ein
Sitzungstoken; nur im vertrauenswürdigen WLAN verwenden und die Kopplung nach dem Einsatz beenden. Alternativ
kann ein Bluetooth-HID-Scanner direkt mit dem Mobilgerät gekoppelt und als Tastatureingabe verwendet werden.

## Cloud-Synchronisierung zwischen PCs

Die Cloud-Funktion verwendet Supabase. SQLite bleibt die lokale Offline-Datenbank. Änderungen werden bei
Cloud-Verbindung automatisch beim Start und anschließend etwa alle fünf Minuten abgeglichen; **Jetzt
synchronisieren** startet einen sofortigen Abgleich. Bei Änderungen desselben Datensatzes auf mehreren PCs
fragt die App, ob die lokale oder die Cloud-Version behalten werden soll. Leere/neu installierte PCs laden
den bereits vorhandenen Cloud-Bestand herunter.

Für die Ersteinrichtung wird ein Supabase-Projekt benötigt:

1. Erstelle ein Projekt im Supabase-Dashboard und kopiere Projekt-URL sowie den öffentlichen Publishable-/anon-Key.
2. Führe `supabase\migrations\202611230001_eventtech_sync.sql` einmal im Supabase **SQL Editor** aus.
3. Installiere die Supabase CLI und führe im Projektordner `supabase login` sowie
   `supabase link --project-ref <Projekt-ID>` aus.
4. Erzeuge in **Einstellungen → Cloud-Synchronisierung** einen Organisationsschlüssel. Trage denselben Wert in
   den Supabase Function Secrets als `EVENTTECH_SHARED_KEY` ein.
5. Veröffentliche den Endpunkt mit `supabase functions deploy eventtech-sync --no-verify-jwt`.
6. Trage Projekt-URL, öffentlichen Key und denselben Organisationsschlüssel unter
   **Einstellungen → Cloud-Synchronisierung** auf jedem PC ein. Auf dem ersten PC mit dem vorhandenen
   Inventar auf **Jetzt synchronisieren** klicken; weitere PCs laden diesen Bestand beim ersten Sync.

Die App speichert den Organisationsschlüssel pro Windows-Benutzer mit DPAPI verschlüsselt. Den
`service_role`-Key niemals in der App, im Quellcode oder in einer Nachricht eintragen; die Edge Function
verwendet ihn nur serverseitig. Alle PCs mit dem gemeinsamen Schlüssel erhalten Zugriff auf denselben Bestand.
Bei Verlust des Schlüssels muss `EVENTTECH_SHARED_KEY` im Supabase-Dashboard ersetzt und auf allen PCs
aktualisiert werden.

Unter **Einstellungen → Cloud-Synchronisierung** können die Supabase-Verbindungseinstellungen als
passwortgeschützte JSON-Datei exportiert und auf einem anderen Gerät importiert werden. Die Datei enthält
URL, öffentlichen Schlüssel und – falls vorhanden – den Organisationsschlüssel, verschlüsselt mit AES-GCM.
Verwende ein starkes Dateipasswort mit mindestens 12 Zeichen und bewahre es getrennt von der Datei auf.
Nach dem Import speichert die Desktop-App den Organisationsschlüssel wieder im geschützten Windows- bzw.
Linux-Schlüsselspeicher.

## GitHub-Pages-Web-App

Die browserbasierte App im Ordner `web` unterstützt Anmeldung, Artikelsuche und -bearbeitung,
Lagerstruktur und Regal-Grafik, DMX-Modi und -Kanäle, Reparaturen, Inventuren, Scan-Verlauf,
optionale automatische Bedienhinweise, Barcode-Erzeugung und Etikettendruck, CSV-Export sowie JSON-Backup.
Die Kamera kann auf unterstützten
Geräten als Scanner verwendet werden; USB-/Bluetooth-HID-Scanner funktionieren über das Eingabefeld.
Fotos und Dokumente lassen sich für Web-Nutzer privat in Supabase speichern.

Die Webseite wird statisch über GitHub Pages ausgeliefert. Artikeldaten bleiben in Supabase; die Webseite
verwendet Supabase Auth und legt weder den `service_role`-Key noch den Desktop-Organisationsschlüssel im
öffentlichen Repository ab. Jeder angemeldete Supabase-Benutzer dieses Projekts kann den gemeinsamen
Inventarbestand lesen und bearbeiten. Daher öffentliche Registrierung deaktivieren und nur vertrauenswürdige
Benutzerkonten anlegen.

### Einrichten

1. Erstelle ein Supabase-Projekt. Für ein neues Projekt führe
   `supabase\migrations\202611230001_eventtech_sync.sql` im SQL Editor aus. Wenn du bereits die Desktop-
   Cloud-Synchronisierung eingerichtet hast, ist diese Basismigration schon vorhanden.
2. Führe anschließend `supabase\migrations\202611240001_eventtech_web_auth.sql` im SQL Editor aus.
   Diese Migration erlaubt angemeldeten Benutzern das Lesen und stellt eine geschützte RPC für Änderungen
   bereit; direkte Schreibrechte auf der Synchronisierungstabelle werden nicht vergeben.
3. Führe `supabase\migrations\202611250001_eventtech_web_attachments.sql` aus. Sie erstellt den privaten
   Datei-Bucket und die Metadatentabelle für Web-Anhänge (bis zu 20 MB pro Datei).
4. Aktiviere in Supabase **Authentication → Providers → Email** und deaktiviere öffentliche Registrierungen.
   Lege die benötigten Benutzer im Supabase-Dashboard unter **Authentication → Users** an bzw. lade sie ein.
5. Ergänze unter **Authentication → URL Configuration** die GitHub-Pages-Adresse als Site URL und erlaubte
   Redirect URL: `https://gggamingkarotte-byte.github.io/EventTechBarcodeManager/`.
6. Öffne im GitHub-Repository **Settings → Pages** und wähle **GitHub Actions** als Build-/Deployment-Quelle.
   Der Workflow `.github\workflows\deploy-web.yml` veröffentlicht den Ordner `web` automatisch nach Pushes
   auf `main`; ein manueller Start ist auch unter **Actions → Deploy inventory web app → Run workflow** möglich.
7. Rufe `https://gggamingkarotte-byte.github.io/EventTechBarcodeManager/` auf. Gib einmalig die Supabase-
   Projekt-URL und den öffentlichen Publishable-/anon-Key ein und melde dich mit einem angelegten Benutzer an.
   Projektkonfiguration und Sitzung werden nur im lokalen Browser gespeichert; auf gemeinsam genutzten
   Geräten nach der Nutzung abmelden.

Die Supabase-Verbindung lässt sich über **Datei exportieren/importieren** als passwortgeschützte JSON-Datei
zwischen Browsern und der Desktop-App übertragen. Die Website-Datei enthält nur Projekt-URL und öffentlichen
Schlüssel; der Browser speichert den Organisationsschlüssel nicht. Beim Import einer Desktop-Datei wird dieser
Schlüssel daher nicht in der Website abgelegt. Das Dateipasswort muss mindestens 12 Zeichen lang sein.

Für die Desktop-App-Cloud-Synchronisierung richte zusätzlich die oben beschriebene Edge Function ein.
Für lokale Web-Entwicklung installiere Node.js, führe `npm ci` und `npm run build` im Ordner `web` aus und
öffne `web` über einen lokalen HTTPS- oder HTTP-Server.

Der Kamerascan benötigt HTTPS und eine Kamera-Freigabe. Die App verwendet die eingebaute Barcode-API, wenn
verfügbar, und einen lokal gebündelten Decoder als Fallback, auch für mobile Browser wie Safari auf dem iPad.
Falls der Kamerazugriff nicht verfügbar ist, kann der Code manuell eingegeben oder mit einem USB-/
Bluetooth-HID-Scanner in das Suchfeld übertragen werden. Kamerabilder werden lokal verarbeitet und nicht
gespeichert oder an Dritte übertragen.

## Regal-Grafik

Unter **Regal-Grafik** kannst du ein Lager und Regal auswählen und die enthaltenen Böden, Boxen und Artikel
überblicken. Artikel lassen sich am Desktop per Drag-and-drop zwischen den angezeigten Boxen verschieben.
Weitere Lager, Regale, Böden und Boxen verwaltest du über **Lagerort verwalten**; Artikel können außerdem
im Bearbeitungsformular einem beliebigen Lagerplatz zugeordnet werden.

## Barcode-Werkstatt

In der **Barcode-Werkstatt** kannst du Code 128-, EAN-13- und QR-Barcodes erstellen, optional einen eigenen
Inhalt angeben, sie Artikeln zuweisen und als PNG herunterladen. Gespeicherte Codes lassen sich zur
Druckliste hinzufügen. Mit **PDF drucken / speichern** öffnet der Browser eine A4-Druckansicht; dort kann
über die Druckfunktion des Browsers ein PDF gespeichert werden.

Synchronisiert werden Artikel, Lagerplätze, Barcodes, DMX-Daten, Reparaturen, Bewegungen, Scan-Verlauf und
Inventuren. Web-Anhänge liegen privat im Supabase-Storage und können von allen angemeldeten Benutzern des
gemeinsamen Inventars gelesen und verwaltet werden. Dateien, die ausschließlich in der Desktop-App importiert
wurden, bleiben lokal und werden nicht automatisch in diesen Web-Speicher übernommen. Der kostenlose Supabase-Tarif hat
änderbare Speicher-/Nutzungslimits und ersetzt kein zusätzliches Backup.

## Tests

```bat
python -m unittest discover -s tests -v
```

## Windows-EXE

`assets\icon.ico` ist enthalten. Mit Python und den in `requirements.txt` genannten Paketen:

```bat
build.bat
```

Die portable Anwendung wird unter `dist\EventTechBarcodeManager` erzeugt. Mit installiertem Inno Setup 6
erstellt das Skript zusätzlich `dist\installer\EventTechBarcodeManager-Setup-1.1.0.exe`. Der Build verwendet
zuerst die lokale virtuelle Umgebung, danach Python aus dem PATH oder den Python-Launcher.

Der Installer erlaubt die Installation nur für den aktuellen Benutzer ohne Administratorrechte oder für alle
Benutzer mit Administratorfreigabe. Datenbank, Dokumente und Backups werden getrennt unter
`%LOCALAPPDATA%\EventTechBarcodeManager` gespeichert und bei der Deinstallation nicht entfernt.

## Raspberry Pi OS (64-Bit)

Der Linux-Installer ist für Raspberry Pi OS 64-Bit (ARM64/aarch64) vorgesehen. Raspberry Pi OS 32-Bit wird
nicht unterstützt, da die benötigten PySide6- und Barcode-Decoder-Pakete dafür keine passenden fertigen
ARM-Wheels bereitstellen.

Nach dem Push der Linux-Paketierung auf `main`:

1. Öffne **Actions → Build Raspberry Pi OS installer** und starte **Run workflow**.
2. Öffne den erfolgreichen Lauf und lade unter **Artifacts** `eventtech-raspberry-pi-os-64-bit` herunter.
3. Entpacke die ZIP-Datei und installiere die `.deb`-Datei per Doppelklick oder im Terminal:
   `sudo apt install ./eventtech-barcode-manager_*_arm64.deb`
4. Starte **EventTech Barcode Manager** über das Anwendungsmenü.

Der Installer richtet GNOME Keyring als geschützten Speicher für Cloud- und Scanner-Schlüssel ein. Die
Cloud-Schlüssel von Windows werden nicht automatisch übertragen; gib den Organisationsschlüssel auf dem
Raspberry Pi erneut ein. USB- und Bluetooth-HID-Scanner funktionieren als Tastatureingabe; die Auswahl
einzelner Scanner-Geräte bleibt Windows vorbehalten.
