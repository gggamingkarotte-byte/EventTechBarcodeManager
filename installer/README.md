# Installer

Der Windows-Installer wird als GitHub-Release-Datei bereitgestellt, damit die
große EXE nicht als reguläre Git-Datei gespeichert werden muss.

## Version 1.1.0

Der Build erzeugt `dist\installer\EventTechBarcodeManager-Setup-1.1.0.exe`.
Ein GitHub-Downloadlink ist erst verfügbar, wenn diese Datei als Asset zum Release v1.1.0 hochgeladen wurde.

## Versionsschema

Die letzte Versionszahl wird höchstens bis 9 erhöht; würde sie auf 10 steigen, beginnt sie wieder bei 0
und die mittlere Versionszahl wird um 1 erhöht (zum Beispiel 1.1.9 → 1.2.0).

Alle Versionen findest du unter
[GitHub Releases](https://github.com/gggamingkarotte-byte/EventTechBarcodeManager/releases).

## Raspberry Pi OS

Für Raspberry Pi OS 64-Bit wird ein ARM64-`.deb`-Paket über den GitHub-Actions-Workflow
**Build Raspberry Pi OS installer** erstellt. Lade das Paket nach einem erfolgreichen Lauf aus dessen
**Artifacts** herunter. Raspberry Pi OS 32-Bit wird von den aktuellen GUI- und Barcode-Abhängigkeiten
nicht unterstützt.
