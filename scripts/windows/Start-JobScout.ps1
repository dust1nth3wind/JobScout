#Requires -Version 5.1
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet('Xinning', 'Harish')]
    [string]$Person,
    [ValidateRange(1, 65535)]
    [int]$Port = 8000,
    [ValidateRange(1, 300)]
    [int]$StartupTimeoutSeconds = 30,
    [switch]$NoBrowser,
    [switch]$NoPause
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Enter-StateGate {
    param([System.Threading.Mutex]$Gate)
    try {
        if (-not $Gate.WaitOne(10000)) {
            throw 'Der andere Starter antwortet nicht. Bitte dessen Fenster schliessen und erneut starten.'
        }
    }
    catch [System.Threading.AbandonedMutexException] {
        # An abandoned mutex is already acquired by this thread.
    }
}

function Assert-PortAvailable {
    param([int]$LocalPort)
    # Inspect listeners, rather than exclusively binding a probe socket: closed
    # HTTP connections in TIME_WAIT must not prevent an immediate restart.
    $listeners = [System.Net.NetworkInformation.IPGlobalProperties]::GetIPGlobalProperties().GetActiveTcpListeners()
    foreach ($endpoint in $listeners) {
        if ($endpoint.Port -eq $LocalPort -and (
                $endpoint.Address.Equals([System.Net.IPAddress]::Loopback) -or
                $endpoint.Address.Equals([System.Net.IPAddress]::Any) -or
                $endpoint.Address.Equals([System.Net.IPAddress]::IPv6Any))) {
            throw "Port $LocalPort ist bereits belegt. Bitte den bisherigen Server in seinem Fenster beenden."
        }
    }
}

function Invoke-JobScoutStep {
    param(
        [string]$Title,
        [string]$UvExecutable,
        [string[]]$Arguments,
        [string]$ProjectRoot,
        [switch]$AllowPartial
    )
    Write-Host "`n$Title"
    $command = [JobScout.Windows.OwnedProcess]::Start($UvExecutable, $Arguments, $ProjectRoot)
    try {
        while (-not $command.WaitForExit(250)) {
            # Return to PowerShell regularly so Ctrl+C can execute finally.
        }
        $commandExitCode = $command.ExitCode
    }
    finally {
        $command.Dispose()
    }
    if ($commandExitCode -eq 0) {
        return
    }
    if ($AllowPartial -and $commandExitCode -eq 2) {
        Write-Warning 'Einzelne Jobquellen sind ausgefallen. Das Dashboard zeigt die verfuegbaren Ergebnisse.'
        return
    }
    throw "$Title fehlgeschlagen (Exitcode $commandExitCode). Siehe Ausgabe oben."
}

function Test-DashboardReady {
    param([string]$Url, [int]$TimeoutMilliseconds)
    $response = $null
    $request = [System.Net.HttpWebRequest]::Create($Url)
    $request.Proxy = $null
    $request.AllowAutoRedirect = $false
    $request.Timeout = $TimeoutMilliseconds
    $request.ReadWriteTimeout = $TimeoutMilliseconds
    try {
        $response = $request.GetResponse()
        return ($response.StatusCode -eq [System.Net.HttpStatusCode]::OK)
    }
    catch [System.Net.WebException] {
        if ($null -ne $_.Exception.Response) {
            $_.Exception.Response.Close()
        }
        return $false
    }
    finally {
        if ($null -ne $response) {
            $response.Close()
        }
    }
}

$instance = $null
$gate = $null
$ready = $null
$restart = $null
$server = $null
$ownsInstance = $false
$launcherExitCode = 0

try {
    if ($env:OS -ne 'Windows_NT') {
        throw 'Dieser Starter benoetigt Windows 10/11 mit Windows PowerShell 5.1.'
    }
    $Person = if ($Person -ieq 'Xinning') { 'Xinning' } else { 'Harish' }
    $profileId = if ($Person -eq 'Xinning') { 'friend-a' } else { 'friend-b' }
    $projectRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\..'))
    $configPath = Join-Path $projectRoot ('config\{0}.local.toml' -f $Person.ToLowerInvariant())
    $url = 'http://127.0.0.1:{0}/?profile={1}' -f $Port, $profileId

    # No PID files: Windows releases these handles when a starter crashes or closes.
    $userSid = [System.Security.Principal.WindowsIdentity]::GetCurrent().User.Value
    $identity = '{0}|{1}|{2}' -f $userSid, $projectRoot.TrimEnd('\').ToLowerInvariant(), $Person
    $sha = [System.Security.Cryptography.SHA256]::Create()
    try {
        $digest = $sha.ComputeHash([System.Text.Encoding]::UTF8.GetBytes($identity))
        $name = 'Local\JobScout-' + ([System.BitConverter]::ToString($digest)).Replace('-', '')
    }
    finally {
        $sha.Dispose()
    }
    $instance = [System.Threading.Mutex]::new($false, ($name + '-Instance'))
    $gate = [System.Threading.Mutex]::new($false, ($name + '-StateGate'))
    $ready = [System.Threading.EventWaitHandle]::new($false, [System.Threading.EventResetMode]::ManualReset, ($name + '-Ready'))
    $restart = [System.Threading.EventWaitHandle]::new($false, [System.Threading.EventResetMode]::AutoReset, ($name + '-Restart'))
    try {
        $ownsInstance = $instance.WaitOne(0)
    }
    catch [System.Threading.AbandonedMutexException] {
        $ownsInstance = $true
    }

    if (-not $ownsInstance) {
        Enter-StateGate $gate
        try {
            if ($ready.WaitOne(0)) {
                [void]$restart.Set()
                Write-Host "JobScout fuer $Person wird im bisherigen Fenster neu gestartet."
            }
            else {
                Write-Host "Eine Aktualisierung fuer $Person laeuft bereits. Bitte im bisherigen Fenster warten."
            }
        }
        finally {
            $gate.ReleaseMutex()
        }
        if (-not $NoPause) {
            Start-Sleep -Seconds 3
        }
        return
    }

    Enter-StateGate $gate
    try {
        [void]$ready.Reset()
        [void]$restart.Reset()
    }
    finally {
        $gate.ReleaseMutex()
    }

    if (-not (Test-Path -LiteralPath (Join-Path $projectRoot 'pyproject.toml') -PathType Leaf)) {
        throw 'Der JobScout-Projektordner fehlt. Bitte das Icon-Setup im vollstaendigen Projekt erneut ausfuehren.'
    }
    if (-not (Test-Path -LiteralPath $configPath -PathType Leaf)) {
        throw "Die private Konfiguration fehlt: $configPath. Bitte die passende Datei zuerst hier ablegen."
    }
    $uvCommand = Get-Command uv.exe -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($null -eq $uvCommand) {
        throw 'uv.exe wurde nicht gefunden. Bitte uv installieren und danach ein neues Startfenster oeffnen: https://docs.astral.sh/uv/getting-started/installation/'
    }
    $uvExecutable = $uvCommand.Source
    if ($null -eq ('JobScout.Windows.OwnedProcess' -as [type])) {
        Add-Type -Path (Join-Path $PSScriptRoot 'JobScout.Process.cs')
    }
    Set-Location -LiteralPath $projectRoot
    $Host.UI.RawUI.WindowTitle = "JobScout - $Person"

    while ($true) {
        Assert-PortAvailable $Port
        Invoke-JobScoutStep -Title '1/4 Pakete aktualisieren: uv sync' -UvExecutable $uvExecutable -Arguments @('sync') -ProjectRoot $projectRoot
        Invoke-JobScoutStep -Title '2/4 Konfiguration pruefen' -UvExecutable $uvExecutable -Arguments @('run', 'jobscout', 'config', 'check', '--config', $configPath) -ProjectRoot $projectRoot
        Invoke-JobScoutStep -Title '3/4 Stellen aktualisieren' -UvExecutable $uvExecutable -Arguments @('run', 'jobscout', 'scan', '--config', $configPath, '--profile', $profileId) -ProjectRoot $projectRoot -AllowPartial

        Assert-PortAvailable $Port
        Write-Host "`n4/4 Dashboard starten"
        $server = [JobScout.Windows.OwnedProcess]::Start($uvExecutable, @('run', 'jobscout', 'serve', '--config', $configPath, '--host', '127.0.0.1', '--port', [string]$Port), $projectRoot)
        $watch = [System.Diagnostics.Stopwatch]::StartNew()
        $deadlineMilliseconds = $StartupTimeoutSeconds * 1000
        $dashboardReady = $false
        while ($watch.ElapsedMilliseconds -lt $deadlineMilliseconds) {
            $attemptStarted = $watch.ElapsedMilliseconds
            if ($server.HasExited) {
                throw "Der Dashboardserver wurde vor dem Start beendet (Exitcode $($server.ExitCode))."
            }
            $remaining = $deadlineMilliseconds - [int]$watch.ElapsedMilliseconds
            $requestTimeout = [Math]::Max(1, [Math]::Min(250, $remaining))
            if ((Test-DashboardReady -Url $url -TimeoutMilliseconds $requestTimeout) -and -not $server.HasExited) {
                $dashboardReady = $true
                break
            }
            $untilNextCheck = 250 - [int]($watch.ElapsedMilliseconds - $attemptStarted)
            $delay = [Math]::Min($untilNextCheck, ($deadlineMilliseconds - [int]$watch.ElapsedMilliseconds))
            if ($delay -gt 0) {
                Start-Sleep -Milliseconds $delay
            }
        }
        if (-not $dashboardReady) {
            throw "Das Dashboard ist nach $StartupTimeoutSeconds Sekunden nicht erreichbar: $url"
        }

        Enter-StateGate $gate
        try {
            [void]$restart.Reset()
            [void]$ready.Set()
        }
        finally {
            $gate.ReleaseMutex()
        }
        Write-Host "Dashboard bereit: $url"
        Write-Host 'Dieses Fenster offen lassen. Ein erneuter Doppelklick aktualisiert; Schliessen beendet den Server.'
        if (-not $NoBrowser) {
            try {
                Start-Process -FilePath $url
            }
            catch {
                Write-Warning "Der Browser konnte nicht geoeffnet werden. Bitte diese Adresse selbst oeffnen: $url"
            }
        }

        while (-not $server.HasExited) {
            if ($restart.WaitOne(250)) {
                break
            }
        }
        Enter-StateGate $gate
        try {
            [void]$ready.Reset()
            [void]$restart.Reset()
        }
        finally {
            $gate.ReleaseMutex()
        }
        if ($server.HasExited) {
            throw "Der Dashboardserver wurde beendet (Exitcode $($server.ExitCode)). Bitte erneut starten."
        }
        Write-Host "`nDashboard beenden und alle Schritte erneut ausfuehren ..."
        $server.Stop()
        $server.Dispose()
        $server = $null
    }
}
catch {
    $launcherExitCode = 1
    Write-Host "`nFehler: $($_.Exception.Message)" -ForegroundColor Red
}
finally {
    if ($ownsInstance) {
        if ($null -ne $ready -and $null -ne $restart -and $null -ne $gate) {
            try {
                Enter-StateGate $gate
                try {
                    [void]$ready.Reset()
                    [void]$restart.Reset()
                }
                finally {
                    $gate.ReleaseMutex()
                }
            }
            catch {
                Write-Warning 'Der Startstatus konnte nicht zurueckgesetzt werden.'
            }
        }
        if ($null -ne $server) {
            try {
                $server.Dispose()
            }
            catch {
                $launcherExitCode = 1
                Write-Host "Fehler beim Beenden des Servers: $($_.Exception.Message)" -ForegroundColor Red
            }
        }
        if ($null -ne $instance) {
            $instance.ReleaseMutex()
        }
    }
    foreach ($handle in @($restart, $ready, $gate, $instance)) {
        if ($null -ne $handle) {
            $handle.Dispose()
        }
    }
}

if ($launcherExitCode -ne 0 -and -not $NoPause) {
    [void](Read-Host 'Zum Schliessen Enter druecken')
}
exit $launcherExitCode
