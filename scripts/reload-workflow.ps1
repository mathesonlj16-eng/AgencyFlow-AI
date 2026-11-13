# Re-import and publish the AgencyFlow workflow into the running demo n8n (after editing it).
$ErrorActionPreference = 'Stop'
Set-Location (Split-Path $PSScriptRoot -Parent)
docker cp n8n/agencyflow-reviewed-webhook.json agencyflow-n8n:/tmp/agencyflow.json
docker exec agencyflow-n8n n8n import:workflow --input=/tmp/agencyflow.json | Out-Null
docker exec agencyflow-n8n n8n publish:workflow --id=AgFlowReceiverV2 | Out-Null
docker restart agencyflow-n8n | Out-Null
for ($i = 0; $i -lt 60; $i++) {
    try { Invoke-WebRequest 'http://localhost:5679/webhook/agencyflow-tasks' -UseBasicParsing -TimeoutSec 3 | Out-Null; Write-Host 'Workflow reloaded.'; exit 0 } catch { Start-Sleep 2 }
}
throw 'Workflow did not come back. Check: docker logs agencyflow-n8n'
