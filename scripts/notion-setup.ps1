# OWNER ONLY. Points the demo n8n at your sandbox Notion workspace.
#   Create a new data source under a page shared with the integration (writes one database to Notion):
#     ./scripts/notion-setup.ps1 -ParentPageId 0123abcd... [-Enable]
#   Or reuse an existing AgencyFlow data source:
#     ./scripts/notion-setup.ps1 -DataSourceId 0123abcd... [-Enable]
#   Turn writes off again at any time:
#     ./scripts/notion-setup.ps1 -DataSourceId <same id>          (no -Enable)
# Writes to Notion stay OFF until you pass -Enable here AND start the app with NOTION_SYNC_ENABLED=1.
param([string]$ParentPageId, [string]$DataSourceId, [switch]$Enable, [string]$N8n = 'http://localhost:5679')
$ErrorActionPreference = 'Stop'
if (-not $ParentPageId -and -not $DataSourceId) { throw 'Pass -ParentPageId (to create the data source) or -DataSourceId (to reuse one).' }
$body = @{ api_base = 'https://api.notion.com/v1'; notion_version = '2025-09-03'; enable = [bool]$Enable }
if ($DataSourceId) { $body.data_source_id = $DataSourceId } else { $body.parent_page_id = $ParentPageId }
try {
    $r = Invoke-RestMethod "$N8n/webhook/agencyflow-notion-setup" -Method Post -ContentType 'application/json' -Body ($body | ConvertTo-Json) -TimeoutSec 120
    Write-Host "Configured. Data source: $($r.data_source_id). Writes enabled: $($r.enabled)"
    if ($r.enabled) { Write-Host 'Start the app with $env:NOTION_SYNC_ENABLED = "1" to show the Sync to Notion button.' }
} catch {
    $detail = $_.ErrorDetails.Message
    throw "Setup failed: $detail"
}
