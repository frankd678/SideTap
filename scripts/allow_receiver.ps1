# Allow inbound connections for the AirPlay receiver only (ApowerMirror).
# Run from an elevated PowerShell. Undo with:  .\allow_receiver.ps1 -Remove
param([switch]$Remove)

$name = "phone-harness AirPlay receiver"
$exes = @(
    "C:\Program Files (x86)\Apowersoft\ApowerMirror\ApowerMirror.exe",
    "C:\Program Files (x86)\Apowersoft\ApowerMirror\ApowerMirrorWiFi.exe"
)

Get-NetFirewallRule -DisplayName $name -ErrorAction SilentlyContinue | Remove-NetFirewallRule
if ($Remove) { Write-Host "Removed '$name'."; return }

foreach ($exe in $exes) {
    if (-not (Test-Path $exe)) { Write-Host "Skipping missing: $exe"; continue }
    New-NetFirewallRule -DisplayName $name -Direction Inbound -Action Allow `
        -Program $exe -Profile Any | Out-Null
    Write-Host "Allowed inbound for $exe"
}
Write-Host "Done."
