$pythonProcs = Get-Process -Name "python" -ErrorAction SilentlyContinue | Where-Object { $_.CommandLine -like "*celery*" }
if ($pythonProcs) {
    $pythonProcs | Select-Object Id,ProcessName | Format-Table -AutoSize
} else {
    Write-Host "No Celery worker found"
}
