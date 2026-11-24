# EventTech Barcode Manager

Lokale Windows-Inventar- und Lagerverwaltungssoftware für Veranstaltungstechnik.

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
2. In **Einstellungen → iPad-Scanner** auf **iPad-Kopplung starten** klicken.
3. Den Kopplungs-QR-Code scannen oder die angezeigte Adresse im Browser des Mobilgeräts öffnen.
4. Einen Barcode mit der Gerätekamera aufnehmen. Nach der Aufnahme wird er automatisch dekodiert und bei Erfolg protokolliert.
5. Bei nicht erkanntem/zu kleinem Barcode zeigt die Seite Hinweise zu Abstand, Ruhe und Beleuchtung.

Die Kameraauswertung erfolgt lokal auf dem PC. Es wird keine externe Scan-Cloud verwendet. Für die Kopplung muss
Windows eingehende Verbindungen im privaten Netzwerk zulassen. Der QR-Code enthält ein Sitzungstoken; nur im
vertrauenswürdigen WLAN verwenden und die Kopplung nach dem Einsatz beenden. Alternativ kann ein Bluetooth-HID-
Scanner direkt mit dem iPad oder Android-Gerät gekoppelt und auf der mobilen Seite als Tastatureingabe verwendet werden.

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

## Regal-Grafik

Unter **Regal-Grafik** kannst du ein Lager und ein Regal auswählen. Regale lassen sich mit Breite,
Gesamthöhe und gewünschter Bodenanzahl anlegen. Unter **Maße / Böden bearbeiten** kannst du danach
Regalmaße sowie Namen und lichte Höhe der einzelnen Böden anpassen oder leere Böden hinzufügen.
Ziehe Boxen auf einen anderen Boden und Artikel/Lampen auf eine andere Box, um sie innerhalb des Regals
umzulagern. Mit **Umlagern …** verschiebst du sie auch zwischen verschiedenen Regalen desselben Lagers.

## Barcode-Dokumente

Unter **Barcode-Programmierung → PDF gestalten** lassen sich mehrere gleiche oder unterschiedliche Codes
mit eigener Bezeichnung und Skalierung zu einem PDF hinzufügen. In der A4-Vorschau kannst du einen Barcode
mit der Maus verschieben und ihn an den Eckgriffen wie ein Bild in Word proportional vergrößern oder
verkleinern. Gerätename und Barcode-Wert bleiben dabei mittig am Barcode ausgerichtet und skalieren mit.
Ein Rahmen um die Etiketten wird nicht gedruckt. Der Gerätename steht über dem Barcode, der Barcode-Wert
genau einmal darunter. Größere Dokumente verteilen sich automatisch auf mehrere Seiten. Einzelne Barcodes
können als PNG heruntergeladen werden. In der **Barcode-Liste** lassen sich gespeicherte Codes ansehen,
konfigurieren, zum PDF hinzufügen, als PNG herunterladen und eigene Einträge löschen. In beiden Listen
kannst du mehrere Codes bzw. Etiketten mit Strg/Umschalt auswählen. Mehrere Codes aus der Barcode-Liste
lassen sich gemeinsam zum PDF hinzufügen; für ausgewählte Etiketten im PDF-Raster kann die Skalierung
mit einem Klick gemeinsam geändert werden.

Synchronisiert werden Artikel, Lagerplätze, Barcodes, DMX-Daten, Reparaturen, Bewegungen, Scan-Verlauf und
Inventuren. Fotos und lokal gespeicherte Dokumentdateien werden derzeit **nicht** in die Cloud kopiert;
sie bleiben nur auf dem PC verfügbar, auf dem sie importiert wurden. Der kostenlose Supabase-Tarif hat
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

Die portable Anwendung wird unter `dist\EventTechBarcodeManager` erzeugt. Wenn Inno Setup 6 installiert ist,
erstellt das Skript zusätzlich `dist\installer\EventTechBarcodeManager-Setup-1.0.6.exe`.

Der Installer erlaubt die Installation nur für den aktuellen Benutzer ohne Administratorrechte oder für alle
Benutzer mit Administratorfreigabe. Datenbank, Dokumente und Backups werden getrennt unter
`%LOCALAPPDATA%\EventTechBarcodeManager` gespeichert und bei der Deinstallation nicht entfernt.
