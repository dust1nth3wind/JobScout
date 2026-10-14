# JobScout

JobScout sammelt öffentliche Stellenanzeigen von Greenhouse, Lever, Ashby, Recruitee und SmartRecruiters, aus öffentlichen Jobboard-Übersichten beziehungsweise XML-Sitemaps sowie von direkt eingetragenen Jobseiten. Anschließend vereinheitlicht die Anwendung die Anzeigen und bewertet sie anhand der Profile in der Konfigurationsdatei. Die Bewertung ist regelbasiert und funktioniert ohne LLM.

## Zwei lokale Setups für Xinning und Harish

Beide Setups verwenden denselben Code aus `main`, aber eigene private Konfigurationen und Datenbanken. Die Dateien `config/xinning.local.toml`, `config/harish.local.toml` und `data/` werden nicht in Git gespeichert. Die bisherige gemeinsame `config/jobscout.toml` und `data/jobscout.sqlite3` bleiben als lokale Backups erhalten. Für die getrennten Setups deshalb bei jedem Befehl `--config` angeben.

Nach einem Code-Update im Ordner `JobScout`:

```text
git pull
uv sync
uv run jobscout config check --config config/xinning.local.toml
uv run jobscout config check --config config/harish.local.toml
uv run jobscout scan --config config/xinning.local.toml --profile friend-a
uv run jobscout scan --config config/harish.local.toml --profile friend-b
```

Falls der bisherige gemeinsame Server noch Port 8000 belegt, beendet ihn zuerst in seinem Terminal mit `Ctrl+C`. Die Dashboards danach bei Bedarf in zwei getrennten Terminals starten:

```text
uv run jobscout serve --config config/xinning.local.toml --port 8000
uv run jobscout serve --config config/harish.local.toml --port 8001
```

Xinning erreicht ihr unter [http://127.0.0.1:8000](http://127.0.0.1:8000), Harish unter [http://127.0.0.1:8001](http://127.0.0.1:8001). Beide Server laufen nur, solange ihre Terminalprozesse laufen; mit `Ctrl+C` beendet ihr sie. `serve` scannt nicht automatisch. Auch spätere Aktualisierungen der Stellen erfolgen nur durch die beiden Scan-Befehle oben. Kopiert bei Updates die Beispielkonfiguration nicht über eure privaten Dateien.

## Windows: Aktualisierung per Desktop-Icon

Auf dem jeweiligen Windows-Rechner erstellt ein Doppelklick auf `scripts\windows\Setup-Icons.cmd` das persönliche Desktop-Icon für Xinning oder Harish. Das Icon installiert die Pakete, prüft die persönliche Konfiguration, aktualisiert die Stellen und öffnet anschließend das Dashboard. Beide verwenden auf ihren getrennten Rechnern Port 8000. Ein weiterer Doppelklick aktualisiert erneut; das Startfenster muss während der Nutzung geöffnet bleiben.

Die [Windows-Anleitung](docs/windows-launcher.md) beschreibt Einrichtung, Beenden und Fehlerbehebung. Die persönliche private Konfiguration muss bereits im Projektordner liegen.

## Voraussetzungen

- macOS oder Windows
- [uv](https://docs.astral.sh/uv/)
- Git, wenn Updates mit `git pull` geladen werden

Das Projekt verwendet Python 3.13. Die Version ist in `.python-version` festgelegt und wird von `uv` unabhängig von der systemweit installierten Python-Version verwaltet.

## Ersteinrichtung

Installiert zuerst die benötigten Pakete:

```text
uv sync
```

Erstellt danach eure persönliche Konfigurationsdatei. Sie wird nicht in Git gespeichert.

macOS:

```text
cp config/jobscout.example.toml config/jobscout.toml
```

Windows PowerShell:

```powershell
Copy-Item config/jobscout.example.toml config/jobscout.toml
```

Öffnet `config/jobscout.toml`, aktiviert die gewünschten Quellen und tragt die jeweiligen Board-IDs ein. Datenbankpfade werden relativ zur Konfigurationsdatei aufgelöst.

Mit `profile_ids = ["friend-a"]` unter einer Quelle ordnet ihr sie ausschließlich Xinning zu; `friend-b` gehört Harish. Ohne `profile_ids` gilt die Quelle für alle Profile. Ein gezielter Scan mit `uv run jobscout scan --profile friend-a` ruft nur für dieses Profil freigegebene Quellen ab.

Mit `preferred_skill_groups` lassen sich alternative Bezeichnungen für dieselbe Technologie zusammenfassen, zum Beispiel `[["nx", "siemens nx"], ["fem", "fea"]]`. Bevorzugte Branchen werden in `preferred_industry_groups` eingetragen. Pro Technologiegruppe wird höchstens ein Treffer gezählt; eine passende bevorzugte Branche erhält die volle Branchenpunktzahl.

Für öffentliche Jobbörsen verwendet ihr `provider = "job_board"`. `listing_urls` enthält HTML-Übersichten oder XML-Sitemaps, `job_url_prefixes` begrenzt die erkannten Detailseiten und `url_term_groups` filtert die URLs vor dem Abruf. Aus jeder Gruppe muss mindestens ein Begriff vorkommen. Mit `excluded_url_terms` und `excluded_seniorities` lassen sich beispielsweise Praktika und Trainee-Stellen schon bei der Erfassung ausschließen.

Wenn eine Anzeige ausdrücklich erwähnt, dass Visa-Sponsoring verfügbar oder nicht verfügbar ist oder bereits eine Arbeitserlaubnis verlangt wird, erscheint dies als neutraler Hinweis in den Treffergründen. Die Stelle wird dadurch nicht automatisch ausgeschlossen.

Prüft zum Schluss die Konfiguration:

```text
uv run jobscout config check
```

## Verwendung

Alle aktivierten Quellen abrufen und die Ergebnisse bewerten:

```text
uv run jobscout scan
```

Den Abruf auf ein Profil und eine Quelle beschränken:

```text
uv run jobscout scan --profile friend-a --source example-greenhouse
```

Das lokale Dashboard starten:

```text
uv run jobscout serve
```

Das Dashboard läuft unter [http://127.0.0.1:8000](http://127.0.0.1:8000). Der Server ist standardmäßig nur auf dem eigenen Computer erreichbar und besitzt keine Anmeldung.

Für die Konfigurationsdatei gilt diese Reihenfolge: `--config`, danach `JOBSCOUT_CONFIG`, danach `config/jobscout.toml`. Bei einer Ausführung über die Aufgabenplanung des Betriebssystems sollten absolute Pfade verwendet werden.

## Tests

```text
uv run pytest
```

Die normalen Tests verwenden gespeicherte Beispieldaten und `httpx.MockTransport`. Sie greifen nicht auf die echten ATS-Schnittstellen zu.

## Daten und Datenschutz

Das Repository ist öffentlich. `config/jobscout.toml`, Datenbanken, WAL-Dateien und Protokolle werden von Git ignoriert. Nur `config/jobscout.example.toml` ist für die Versionsverwaltung vorgesehen. Vor Änderungen am Datenbankschema oder größeren Aktualisierungen sollte die SQLite-Datenbank gesichert werden.

Beispiele für die automatische Ausführung unter macOS und Windows stehen in [docs/scheduling.md](docs/scheduling.md).
