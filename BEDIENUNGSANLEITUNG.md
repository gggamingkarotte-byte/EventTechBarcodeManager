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

## Barcode scannen

Der **Lesemodus** nimmt USB- und Bluetooth-HID-Barcodes entgegen. Der Barcode kann gescannt oder
manuell eingegeben und mit Enter bestätigt werden. Unter **Einstellungen** lassen sich Präfix und
Suffix des Scanners festlegen.

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
