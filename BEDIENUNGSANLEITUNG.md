# Bedienungsanleitung

## Start

Die Anwendung starten. Beim ersten Start wird die Datenbank unter
`%LOCALAPPDATA%\EventTechBarcodeManager` automatisch angelegt.

## Artikel anlegen

1. **Artikel** öffnen.
2. **Hinzufügen** wählen.
3. Artikelname, Hersteller, Modell, Kategorie, Anzahl, Barcode und Status eingeben.
4. **OK** bestätigen.

Es darf keine zweite Zuweisung desselben Barcodes geben.
Beim Erstellen oder Bearbeiten kann **Bedienhinweise automatisch ergänzen** aktiviert werden. Die App fügt
allgemeine Hinweise passend zur Kategorie und einen Suchlink für die Herstelleranleitung in die technischen
Daten ein. Die Hinweise werden beim Bearbeiten aktualisiert und nicht dupliziert. Der Suchlink öffnet eine
Google-Suche; Hersteller und Modell werden erst beim Anklicken übertragen, und das gefundene Handbuch sollte
vor der Verwendung mit dem exakten Gerät abgeglichen werden.

Über **DMX-Modi und Kanäle bearbeiten** können im Artikel-Dialog DMX-Modi und deren Kanalbelegung gepflegt
werden. Pro Kanal lassen sich Nummer, Funktion, Minimal-/Maximalwert und Beschreibung festlegen. Die Änderungen
werden mit dem Artikel gespeichert.

## Barcode scannen

Der **Lesemodus** nimmt USB- und Bluetooth-HID-Barcodes entgegen. Auf dem **Dashboard** kann zusätzlich
eine PC-Kamera gestartet werden; erkannte Barcodes werden automatisch eingelesen. Unter **Einstellungen**
lassen sich Präfix und Suffix des Scanners festlegen.

Für einen PC-Kamerascan **Live-Kamera starten** auf dem Dashboard wählen und den Barcode mittig ins Bild halten.
Beim Wechsel zu einer anderen Seite wird die Kamera aus Datenschutzgründen automatisch ausgeschaltet.

## iPad oder Android als Scanner

1. Windows-PC und Mobilgerät mit demselben lokalen WLAN verbinden.
2. Unter **Einstellungen → iPad / Android** die Kopplung starten und den QR-Code öffnen.
3. Das **Kamera-Zertifikat installieren** laden. Auf dem iPad das Profil unter **Einstellungen → Profil geladen** installieren und unter **Allgemein → Info → Zertifikatsvertrauenseinstellungen** vollständig aktivieren.
4. Die HTTPS-Adresse öffnen, **Live-Kamera starten** antippen und den Barcode mittig ins Bild halten. Erkannte Codes werden automatisch übertragen.
5. Alternativ **Einzelnes Foto aufnehmen** verwenden.

Die Kameraauswertung erfolgt lokal auf dem PC. Das Zertifikat bleibt lokal auf diesem PC; nach einer Änderung der Netzwerkadresse kann eine erneute Installation nötig sein. Die Kopplungsadresse enthält ein Sitzungstoken und sollte nur im vertrauenswürdigen WLAN verwendet werden.

## Lager verwalten

Unter **Lagerverwaltung** werden Lager, erste Ebene und Box angelegt. Weitere Boxen können über
**Box anlegen** erstellt werden. **Artikel umlagern** verbindet einen Artikel mit einer neuen Box.

Jeder Lagerplatz erhält einen eindeutigen eigenen Barcode.

## Inventur

1. Lager auswählen und **Inventur starten** wählen.
2. Artikelbarcodes scannen.
3. Sollbestand und Istbestand werden verglichen.
4. **Inventurbericht exportieren** erstellt eine CSV-Datei.

## Reparaturen

Unter **Reparaturen** können mehrere Reparatureinträge pro Artikel gespeichert werden.
Defekte Geräte und Geräte in Reparatur werden deutlich markiert.

## Etiketten

Unter **Barcode-Programmierung** einen Eindeutigen Barcode generieren, einem Artikel zuweisen
und als PDF-Etikett speichern.

## Datensicherung

Unter **Einstellungen** kann ein Backup erstellt oder eine vorhandene SQLite-Sicherung wiederhergestellt
werden. Regelmäßige manuelle Sicherungen werden empfohlen.
