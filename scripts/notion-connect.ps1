# OWNER ONLY. Stores your sandbox Notion integration secret in the demo n8n's encrypted credential
# store. The secret is read with a hidden prompt, piped straight into the container, and never
# written to this repo, a log, or the screen.
#
# Before running: in Notion, create an internal integration in a SEPARATE, FICTIONAL sandbox
# workspace, give it "Read content", "Update content" and "Insert content" only, and share one
# empty page with it (the database will be created under that page by notion-setup.ps1).
$ErrorActionPreference = 'Stop'
$secure = Read-Host -Prompt 'Paste the sandbox Notion internal integration secret (input hidden)' -AsSecureString
$plain = [Runtime.InteropServices.Marshal]::PtrToStringBSTR([Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure))
if ($plain -notmatch '^(ntn_|secret_)[A-Za-z0-9]{20,}$') { $plain = $null; throw 'That does not look like a Notion internal integration secret (ntn_... or secret_...).' }
$cred = ConvertTo-Json -Compress @(@{ id = 'AgFlowNotionCred1'; name = 'AgencyFlow Notion (sandbox)'; type = 'httpHeaderAuth';
                                      data = @{ name = 'Authorization'; value = "Bearer $plain" } })
$plain = $null
# The marker stops the mock-based integration tests from ever overwriting this real credential.
$cred | docker exec -i agencyflow-n8n sh -c 'umask 077; cat > /home/node/.n8n/c.json && n8n import:credentials --input=/home/node/.n8n/c.json >/dev/null; rm -f /home/node/.n8n/c.json; touch /home/node/.n8n/agencyflow-real-notion'
$cred = $null
if ($LASTEXITCODE -ne 0) { throw 'Import failed. Is the demo n8n running? (scripts/start-demo-n8n.ps1)' }
Write-Host 'Stored in n8n (encrypted). Next: scripts/notion-setup.ps1 -ParentPageId <page id shared with the integration>'
