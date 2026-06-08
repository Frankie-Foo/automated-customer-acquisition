param(
  [string]$Config = "config.yaml",
  [string]$Prefix = "sales",
  [int]$Count = 30,
  [int]$SourceLimit = 100,
  [int]$SendLimit = 100
)

$ErrorActionPreference = "Stop"
$passwords = @()

for ($i = 1; $i -le $Count; $i++) {
  $username = "{0}{1:D2}" -f $Prefix, $i
  $displayName = "销售{0:D2}" -f $i
  $bytes = New-Object byte[] 18
  [System.Security.Cryptography.RandomNumberGenerator]::Fill($bytes)
  $password = [Convert]::ToBase64String($bytes).TrimEnd("=")
  .\.venv\Scripts\python -m sales_automation.cli --config $Config user-add `
    --username $username `
    --password $password `
    --display-name $displayName `
    --role sales `
    --source-limit $SourceLimit `
    --send-limit $SendLimit
  $passwords += [pscustomobject]@{
    username = $username
    display_name = $displayName
    password = $password
    source_limit = $SourceLimit
    send_limit = $SendLimit
  }
}

$out = "outputs\sales_users_{0}.csv" -f (Get-Date -Format "yyyyMMdd_HHmmss")
New-Item -ItemType Directory -Force -Path "outputs" | Out-Null
$passwords | Export-Csv -NoTypeInformation -Encoding UTF8 -Path $out
Write-Host "Created users. Password file: $out"
