# JobScout

JobScout sammelt öffentliche Stellenanzeigen von Greenhouse, Lever, Ashby, Recruitee und SmartRecruiters, aus öffentlichen Jobboard-Übersichten beziehungsweise XML-Sitemaps sowie von direkt eingetragenen Jobseiten. Anschließend vereinheitlicht die Anwendung die Anzeigen und bewertet sie anhand der Profile in der Konfigurationsdatei. Die Bewertung ist regelbasiert und funktioniert ohne LLM.

## Für Harish und Xinning: Nach einem Update

Öffnet ein Terminal im Ordner `JobScout` und führt diese Befehle der Reihe nach aus:

```text
git pull
uv sync
uv run jobscout config check
uv run jobscout serve
```

Danach ist das Dashboard unter [http://127.0.0.1:8000](http://127.0.0.1:8000) erreichbar.

Mit `Ctrl+C` beendet ihr den laufenden Server. Wenn ihr nur neue Stellenanzeigen abrufen möchtet, verwendet statt `serve` diesen Befehl:

```text
uv run jobscout scan
```

Wichtig: Kopiert bei einem Update nicht erneut `config/jobscout.example.toml`. Eure persönlichen Einstellungen stehen bereits in `config/jobscout.toml` und bleiben bei `git pull` erhalten. Falls `config check` einen Fehler meldet, vergleicht eure Datei mit `config/jobscout.example.toml` und ergänzt neue Einstellungen.

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
