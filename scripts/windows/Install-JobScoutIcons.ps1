#Requires -Version 5.1
[CmdletBinding()]
param(
    [ValidateSet('Xinning', 'Harish')]
    [string] $Person,
    [string] $DesktopPath
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

try {
    if ($env:OS -ne 'Windows_NT') {
        throw 'Dieses Setup benötigt Windows 10/11 mit Windows PowerShell 5.1.'
    }

    if ([string]::IsNullOrWhiteSpace($Person)) {
        Write-Host 'Für wen soll das Desktop-Icon eingerichtet werden?'
        Write-Host '  1 = Xinning (blaues X)'
        Write-Host '  2 = Harish (grünes H)'
        do {
            $choice = (Read-Host 'Bitte 1 oder 2 eingeben').Trim()
            switch ($choice.ToLowerInvariant()) {
                '1' { $Person = 'Xinning' }
                'xinning' { $Person = 'Xinning' }
                '2' { $Person = 'Harish' }
                'harish' { $Person = 'Harish' }
                default { Write-Host 'Bitte 1 (Xinning) oder 2 (Harish) eingeben.' }
            }
        } while ([string]::IsNullOrWhiteSpace($Person))
    }

    # Normalize case when -Person was supplied in lower case.
    if ($Person -ieq 'Xinning') { $Person = 'Xinning' } else { $Person = 'Harish' }
    $repoRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\..'))
    $configPath = Join-Path $repoRoot ('config\{0}.local.toml' -f $Person.ToLowerInvariant())
    if (-not (Test-Path -LiteralPath $configPath -PathType Leaf)) {
        throw "Die persönliche Konfiguration fehlt: $configPath. Kopiere deine bereits eingerichtete private Konfiguration dorthin und starte das Setup erneut."
    }
    if (-not (Get-Command uv.exe -CommandType Application -ErrorAction SilentlyContinue)) {
        throw 'uv.exe wurde nicht gefunden. Installiere uv von https://docs.astral.sh/uv/ und öffne das Setup danach erneut. uv muss im Windows-PATH erreichbar sein.'
    }

    $starterPath = Join-Path $PSScriptRoot 'Start-JobScout.ps1'
    $iconPath = Join-Path $PSScriptRoot ('icons\{0}.ico' -f $Person.ToLowerInvariant())
    $powershellPath = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
    foreach ($requiredPath in @($starterPath, $iconPath, $powershellPath)) {
        if (-not (Test-Path -LiteralPath $requiredPath -PathType Leaf)) {
            throw "Eine benötigte Datei fehlt: $requiredPath. Prüfe, ob der JobScout-Projektordner vollständig ist."
        }
    }

    $shell = New-Object -ComObject WScript.Shell
    if ([string]::IsNullOrWhiteSpace($DesktopPath)) {
        $DesktopPath = [string] $shell.SpecialFolders.Item('Desktop')
        if ([string]::IsNullOrWhiteSpace($DesktopPath)) {
            throw 'Der Windows-Desktop konnte nicht ermittelt werden.'
        }
    }
    $DesktopPath = [System.IO.Path]::GetFullPath($DesktopPath)
    if (-not (Test-Path -LiteralPath $DesktopPath -PathType Container)) {
        $null = New-Item -ItemType Directory -Path $DesktopPath -Force
    }

    $shortcutPath = Join-Path $DesktopPath ("JobScout – $Person.lnk")
    $shortcut = $shell.CreateShortcut($shortcutPath)
    $shortcut.TargetPath = $powershellPath
    $shortcut.Arguments = '-NoProfile -ExecutionPolicy Bypass -File "{0}" -Person "{1}"' -f $starterPath, $Person
    $shortcut.WorkingDirectory = $repoRoot
    $shortcut.IconLocation = '{0},0' -f $iconPath
    $shortcut.Description = "Stellen für $Person aktualisieren und JobScout öffnen"
    $shortcut.WindowStyle = 1
    $shortcut.Save()

    Write-Host ''
    Write-Host "Desktop-Icon eingerichtet: $shortcutPath" -ForegroundColor Green
    Write-Host 'Doppelklick aktualisiert die Stellen und öffnet http://127.0.0.1:8000.'
    Write-Host 'Das Startfenster bleibt während der Nutzung geöffnet. Schließen beendet den Server.'
    Write-Host 'Falls der Projektordner verschoben wird, dieses Setup erneut ausführen.'
    exit 0
}
catch {
    Write-Host ''
    Write-Host ('Setup fehlgeschlagen: {0}' -f $_.Exception.Message) -ForegroundColor Red
    exit 1
}
