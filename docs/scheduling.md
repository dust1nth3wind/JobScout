# Scheduling JobScout

JobScout does not run a background daemon. Use the operating system scheduler to invoke `jobscout scan`. Always use absolute paths so the scheduler does not depend on an interactive shell or working directory.

Before scheduling, run the exact command manually and confirm that it completes successfully.

## macOS with launchd

Find the absolute `uv` path:

```text
which uv
```

Create `~/Library/LaunchAgents/local.jobscout.scan.plist` and replace every `/absolute/...` placeholder:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>local.jobscout.scan</string>
  <key>ProgramArguments</key>
  <array>
    <string>/absolute/path/to/uv</string>
    <string>run</string>
    <string>--project</string>
    <string>/absolute/path/to/JobScout</string>
    <string>jobscout</string>
    <string>scan</string>
    <string>--config</string>
    <string>/absolute/path/to/JobScout/config/jobscout.toml</string>
  </array>
  <key>StartInterval</key><integer>21600</integer>
  <key>StandardOutPath</key><string>/absolute/path/to/JobScout/logs/scan.log</string>
  <key>StandardErrorPath</key><string>/absolute/path/to/JobScout/logs/scan-error.log</string>
</dict>
</plist>
```

Create the log directory and load the agent:

```text
mkdir -p /absolute/path/to/JobScout/logs
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/local.jobscout.scan.plist
```

`21600` seconds means every six hours.

## Windows Task Scheduler

1. Open **Task Scheduler** and choose **Create Task**.
2. Add a daily trigger and set the desired repeat interval.
3. Add an action **Start a program**.
4. Set **Program/script** to the absolute path of `uv.exe`.
5. Set **Add arguments** to:

```text
run --project "C:\absolute\path\to\JobScout" jobscout scan --config "C:\absolute\path\to\JobScout\config\jobscout.toml"
```

6. Set **Start in** to the absolute JobScout repository directory.

The task exit code is `0` for success, `2` for a partial scan, and `1` when every selected source failed or configuration was invalid.

## Backups

Stop active scans before copying the SQLite database. Back up the main `.sqlite3` file together with any adjacent `-wal` and `-shm` files, or use SQLite's own backup command. Do not place the live database in a cloud-synchronized folder while JobScout is running.
