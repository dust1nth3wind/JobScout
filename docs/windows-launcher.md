# JobScout mit einem Windows-Desktop-Icon starten

## Einmalig einrichten

Voraussetzungen: Windows 10/11 mit Windows PowerShell 5.1, installiertes [uv](https://docs.astral.sh/uv/) im Windows-PATH und ein vollständiger JobScout-Projektordner. Die persönliche Konfiguration muss bereits unter `config/xinning.local.toml` beziehungsweise `config/harish.local.toml` liegen. Administratorrechte sind nicht erforderlich.

1. Öffne im Explorer den JobScout-Ordner und darin `scripts\windows`.
2. Starte `Setup-Icons.cmd` per Doppelklick.
3. Gib **1 für Xinning** oder **2 für Harish** ein.

Das Setup erstellt auf deinem tatsächlichen Windows-Desktop „JobScout – Xinning“ mit einem blauen **X** oder „JobScout – Harish“ mit einem grünen **H**. Es berücksichtigt auch einen umgeleiteten Desktop, zum Beispiel über OneDrive. Erneutes Einrichten aktualisiert dieselbe Verknüpfung. Wenn du den Projektordner verschiebst, führe das Setup danach erneut aus.

## Aktualisieren und öffnen

Doppelklicke auf dein Desktop-Icon. Im sichtbaren Startfenster laufen nacheinander:

1. `uv sync`
2. Konfigurationsprüfung mit deiner persönlichen Konfiguration
3. Scan für dein Profil (`friend-a` für Xinning, `friend-b` für Harish)
4. Start des Dashboards mit deiner persönlichen Konfiguration
5. Öffnen des Standardbrowsers, sobald das Dashboard erreichbar ist

Auf ihren **getrennten Windows-Rechnern** öffnen beide [http://127.0.0.1:8000](http://127.0.0.1:8000). Die Desktop-Starter verwenden beide Port 8000 und können deshalb auf demselben Rechner nicht gleichzeitig laufen. Die manuellen Befehle für zwei Dashboards auf einem Rechner stehen in der [README](../README.md#zwei-lokale-setups-für-xinning-und-harish).

**Lass das Startfenster offen**, solange du das Dashboard verwendest. Schließen des Fensters oder `Ctrl+C` beendet den Server samt Unterprozessen. Ein offener Browser-Tab allein hält das Dashboard nicht am Laufen.

## Erneut starten und Fehler beheben

- Sobald das Dashboard läuft, startet ein weiterer Doppelklick die vollständige Aktualisierung erneut: Der bisherige Server wird zuerst beendet. Zusätzliche Klicks während einer laufenden Aktualisierung zeigen einen Hinweis; sie starten keinen zweiten Scan.
- Falls einzelne Quellen beim Scan scheitern (Exitcode 2), erscheint eine Warnung. Das Dashboard öffnet trotzdem und zeigt die verfügbaren Ergebnisse. Details stehen im Startfenster.
- Fehler bei Paketinstallation oder Konfigurationsprüfung sowie ein vollständig fehlgeschlagener Scan stoppen den Ablauf mit einer sichtbaren Fehlermeldung.
- Belegt bereits ein anderer Prozess Port 8000, wird er nicht beendet. Stoppe den bisherigen JobScout-Server in dessen Terminal mit `Ctrl+C` oder beende die andere Anwendung und starte dein Icon erneut.
- Startet der Server nicht oder ist das Dashboard nach 30 Sekunden nicht erreichbar, bleibt die Fehlermeldung sichtbar. Prüfe die Ausgabe im Startfenster.

Code-Updates lädst du wie bisher im Projektordner, zum Beispiel mit `git pull`. Danach genügt ein Doppelklick auf das Icon; `uv sync` läuft automatisch. Kopiere bei einem Update keine Beispielkonfiguration über deine privaten Dateien. Das Setup und die Starter schreiben diese Dateien nicht um.

## Einrichtung aus PowerShell

Für die direkte Einrichtung ohne Auswahlmenü:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File ".\scripts\windows\Install-JobScoutIcons.ps1" -Person Xinning
# Alternativ: -Person Harish
```

Für Tests lässt sich mit `-DesktopPath "C:\Testordner\Desktop"` ein anderer Zielordner angeben. Die Verknüpfung enthält absolute Pfade; Leerzeichen und Umlaute im Projektpfad werden unterstützt.

## Windows-Tests

Im JobScout-Projektordner:

```powershell
uv run pytest -q tests/test_windows_launcher.py
```

Die Tests verwenden ein simuliertes `uv`, temporäre Konfigurationen und einen lokalen Testserver. Sie rufen keine Jobquellen ab und öffnen keinen Browser.

Die Launcher sind für Windows PowerShell 5.1 entwickelt. Die Windows-Integrationstests benötigen Windows; ein erfolgreicher Testlauf auf macOS bestätigt die native Verknüpfung und Prozessverwaltung nicht.
