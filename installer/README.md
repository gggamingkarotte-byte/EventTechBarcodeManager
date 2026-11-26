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

## Linux (`.deb`)

Der GitHub-Actions-Workflow **Build Linux .deb packages** erstellt native `.deb`-Dateien für:

- `amd64` (64-Bit-x86, z. B. Debian/Ubuntu-PCs)
- `arm64` (64-Bit-ARM, z. B. Raspberry Pi OS 64-Bit)

Lade im erfolgreichen Workflow-Lauf das Artefakt `eventtech-linux-amd64` oder
`eventtech-linux-arm64` herunter und installiere die enthaltene Datei mit
`sudo apt install ./eventtech-barcode-manager_<Version>_<Architektur>.deb`.
32-Bit-Linux und nicht-Debian-basierte Distributionen werden von diesen `.deb`-Paketen nicht unterstützt.
