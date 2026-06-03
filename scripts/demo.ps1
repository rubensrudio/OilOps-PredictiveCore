<#
.SYNOPSIS
  OilOps-PredictiveCore end-to-end demo (PowerShell).
.DESCRIPTION
  Brings the stack up, pushes a vibration telemetry batch for one asset, then
  reads back the prediction, explanation, metrics and audit trail.
  Advisory (RN-06): this system is ADVISORY ONLY. Not safety-rated.
.EXAMPLE
  ./scripts/demo.ps1
  $env:OILOPS_API_KEY = 'secret'; ./scripts/demo.ps1
#>
$ErrorActionPreference = 'Stop'

$ApiUrl  = if ($env:API_URL)  { $env:API_URL }  else { 'http://localhost:8000' }
$AssetId = if ($env:ASSET_ID) { $env:ASSET_ID } else { 'PUMP-001' }
$Headers = @{ 'Content-Type' = 'application/json' }
if ($env:OILOPS_API_KEY) { $Headers['X-API-Key'] = $env:OILOPS_API_KEY }
$Ts = (Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ')

Write-Host '==> 1/5  Bringing the stack up (docker compose up -d --wait)'
docker compose up -d --wait

Write-Host "==> 2/5  Health check: GET $ApiUrl/health"
Invoke-RestMethod -Uri "$ApiUrl/health" -Headers $Headers | ConvertTo-Json -Depth 6

Write-Host "==> 3/5  Pushing telemetry batch for asset $AssetId"
$body = @{
  readings = @(
    @{ asset_id = $AssetId; timestamp = $Ts; metric_name = 'vibration_x'; value = 0.0231; unit = 'm/s2'; source_protocol = 'rest_batch' },
    @{ asset_id = $AssetId; timestamp = $Ts; metric_name = 'vibration_x'; value = 0.0198; unit = 'm/s2'; source_protocol = 'rest_batch' },
    @{ asset_id = $AssetId; timestamp = $Ts; metric_name = 'vibration_x'; value = 0.0265; unit = 'm/s2'; source_protocol = 'rest_batch' }
  )
} | ConvertTo-Json -Depth 6
Invoke-RestMethod -Uri "$ApiUrl/telemetry" -Method Post -Headers $Headers -Body $body | ConvertTo-Json -Depth 6

Write-Host "==> 4/5  Reading current prediction: GET $ApiUrl/predictions/$AssetId"
try {
  Invoke-RestMethod -Uri "$ApiUrl/predictions/$AssetId" -Headers $Headers | ConvertTo-Json -Depth 6
} catch {
  Write-Host "    (no prediction yet for $AssetId — $($_.Exception.Message))"
}

Write-Host "==> 5/5  Observability: /metrics (head) and /audit (first page)"
(Invoke-WebRequest -Uri "$ApiUrl/metrics" -Headers $Headers).Content -split "`n" | Select-Object -First 15
Write-Host '---'
try { Invoke-RestMethod -Uri "$ApiUrl/audit?limit=5" -Headers $Headers | ConvertTo-Json -Depth 6 } catch {}

Write-Host ''
Write-Host '==> Demo complete. Every response carries X-Advisory-Only: true.'
Write-Host '    Tear down with:  make down'
