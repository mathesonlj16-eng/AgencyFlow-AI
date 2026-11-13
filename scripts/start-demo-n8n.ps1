# Starts a throwaway local n8n (Docker) on 127.0.0.1:5679, imports the AgencyFlow receiver
# workflow and publishes it. Approved tasks are stored in the n8n data table "agencyflow_tasks". It uses its own container and volume, so it does not touch
# any other n8n you run. Remove everything with:  docker rm -f agencyflow-n8n; docker volume rm agencyflow_n8n_data
param([string]$Image = 'docker.n8n.io/n8nio/n8n:2.47.4', [int]$Port = 5679)
$ErrorActionPreference = 'Stop'
Set-Location (Split-Path $PSScriptRoot -Parent)

# A private Docker network lets tests reach a local Notion mock by name (http://notion-mock:8787).
if (-not (docker network ls --format '{{.Name}}' | Select-String -Quiet '^agencyflow-net$')) { docker network create agencyflow-net | Out-Null }
if (-not (docker ps -a --format '{{.Names}}' | Select-String -Quiet '^agencyflow-n8n$')) {
    docker volume create agencyflow_n8n_data | Out-Null
    docker run -d --name agencyflow-n8n --network agencyflow-net -p "127.0.0.1:${Port}:5678" -v agencyflow_n8n_data:/home/node/.n8n `
        -e N8N_DIAGNOSTICS_ENABLED=false -e N8N_PERSONALIZATION_ENABLED=false $Image | Out-Null
} else { docker start agencyflow-n8n | Out-Null }

Write-Host 'Waiting for n8n...'
for ($i = 0; $i -lt 60; $i++) {
    try { Invoke-WebRequest "http://localhost:$Port/healthz" -UseBasicParsing -TimeoutSec 2 | Out-Null; break } catch { Start-Sleep 2 }
}
# n8n will not publish a workflow whose credential is missing, so create a placeholder Notion
# credential once. It holds no secret and cannot write anywhere; scripts/notion-connect.ps1 replaces
# it with the owner's real sandbox token. An existing credential is never overwritten.
docker exec agencyflow-n8n n8n export:credentials --id=AgFlowNotionCred1 2>&1 | Out-Null
if ($LASTEXITCODE -ne 0) {
    '[{"id":"AgFlowNotionCred1","name":"AgencyFlow Notion (sandbox)","type":"httpHeaderAuth","data":{"name":"Authorization","value":"Bearer not-configured"}}]' |
        docker exec -i agencyflow-n8n sh -c 'cat > /home/node/.n8n/placeholder.json && n8n import:credentials --input=/home/node/.n8n/placeholder.json; rm -f /home/node/.n8n/placeholder.json'
}
docker cp n8n/agencyflow-reviewed-webhook.json agencyflow-n8n:/tmp/agencyflow.json
docker exec agencyflow-n8n n8n import:workflow --input=/tmp/agencyflow.json
docker exec agencyflow-n8n n8n publish:workflow --id=AgFlowReceiverV2
docker restart agencyflow-n8n | Out-Null
# Ready means the published webhook is registered: the read-back endpoint answers 200.
$ready = $false
for ($i = 0; $i -lt 60 -and -not $ready; $i++) {
    try { Invoke-WebRequest "http://localhost:$Port/webhook/agencyflow-tasks" -UseBasicParsing -TimeoutSec 3 | Out-Null; $ready = $true } catch { Start-Sleep 2 }
}
if (-not $ready) { throw 'n8n started but the AgencyFlow webhooks did not register. Check: docker logs agencyflow-n8n' }
Write-Host "Receiver ready. In the app's shell set:"
Write-Host "  `$env:N8N_WEBHOOK_URL = 'http://localhost:$Port/webhook/agencyflow-reviewed'"
Write-Host "n8n editor (create a local owner account on first visit): http://localhost:$Port"
