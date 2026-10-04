# setup.ps1 - Cleans the GitHub repo and installs the new structure.
#
# Run from the folder containing repo-update\  (your Desktop project folder).
#   powershell -ExecutionPolicy Bypass -File .\repo-update\setup.ps1
#
# What it does:
#   - Clones the repo fresh into a temp folder
#   - Deletes ALL git history (removes client data permanently)
#   - Keeps email_engine.py and verify_harvested_emails.py
#   - Removes the website harvester and every CSV/JSONL
#   - Installs README, PROMPTS, RUNBOOK, .gitignore, etc
#   - Force pushes one clean commit
#
# Your local data on the Desktop is NOT touched. It stays as your backup.

$ErrorActionPreference = "Stop"

$RepoUrl  = "https://github.com/pandush-entrepreneur/email-harvester.git"
$Bundle   = Join-Path $PSScriptRoot "."
$WorkDir  = Join-Path $env:TEMP "email-harvester-clean"

Write-Host ""
Write-Host "==============================================" -ForegroundColor Cyan
Write-Host " Rebuilding: $RepoUrl" -ForegroundColor Cyan
Write-Host "==============================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "This DELETES ALL GIT HISTORY and force pushes one clean commit."
Write-Host "Client CSVs and results will be permanently removed from GitHub."
Write-Host ""
Write-Host "Your local files on the Desktop are NOT touched." -ForegroundColor Green
Write-Host ""

$ok = Read-Host "Type YES to continue"
if ($ok -ne "YES") { Write-Host "Aborted."; exit }

# --- 1. Fresh clone -------------------------------------------------
Write-Host ""
Write-Host "[1/5] Cloning repo..." -ForegroundColor Yellow
if (Test-Path $WorkDir) { Remove-Item -Recurse -Force $WorkDir }
git clone $RepoUrl $WorkDir
if ($LASTEXITCODE -ne 0) { Write-Host "Clone failed." -ForegroundColor Red; exit 1 }
Set-Location $WorkDir

# --- 2. Wipe history ------------------------------------------------
Write-Host ""
Write-Host "[2/5] Deleting git history..." -ForegroundColor Yellow
Remove-Item -Recurse -Force ".git"
Write-Host "      history gone - client data cannot be recovered from GitHub"

# --- 3. Remove what is not needed -----------------------------------
Write-Host ""
Write-Host "[3/5] Removing files..." -ForegroundColor Yellow
$remove = @(
    "website_email_harvester.py",
    "Website Email Harvester.md",
    "website_harvester_output",
    "email_engine_output"
)
foreach ($f in $remove) {
    if (Test-Path $f) { Remove-Item -Recurse -Force $f; Write-Host "      removed $f" }
}
Get-ChildItem -Recurse -Include *.csv,*.jsonl -File | ForEach-Object {
    Remove-Item -Force $_.FullName
    Write-Host "      removed $($_.Name)"
}

# --- 4. Install new files -------------------------------------------
Write-Host ""
Write-Host "[4/5] Installing docs and config..." -ForegroundColor Yellow
$copy = @(".gitignore",".env.example","requirements.txt","README.md",
          "PROMPTS.md","RUNBOOK.md","CLEANUP.md","ENGINE-PATCHES.md",
          "SKILL-Permutation.md")
foreach ($f in $copy) {
    $src = Join-Path $Bundle $f
    if (Test-Path $src) { Copy-Item $src -Destination . -Force; Write-Host "      added $f" }
    else { Write-Host "      MISSING in bundle: $f" -ForegroundColor Red }
}
New-Item -ItemType Directory -Force -Path "clients" | Out-Null
New-Item -ItemType File -Force -Path "clients\.gitkeep" | Out-Null

Write-Host ""
Write-Host "      Final contents:" -ForegroundColor Green
Get-ChildItem | Select-Object -ExpandProperty Name | ForEach-Object { Write-Host "        $_" }

# --- 5. Commit and push ---------------------------------------------
Write-Host ""
Write-Host "[5/5] Committing..." -ForegroundColor Yellow
git init -b main
git add -A

# Safety check
$staged = git diff --cached --name-only
if ($staged -match '\.csv$|\.jsonl$') {
    Write-Host "STOP: a CSV or JSONL is staged. Not pushing." -ForegroundColor Red
    $staged | Where-Object { $_ -match '\.csv$|\.jsonl$' }
    exit 1
}
Write-Host "      verified: no CSV or JSONL staged" -ForegroundColor Green

git commit -m "Clean restructure: engine, docs, gitignore. Client data removed."
git remote add origin $RepoUrl

Write-Host ""
Write-Host "Ready to force push. This overwrites GitHub completely." -ForegroundColor Cyan
$ok2 = Read-Host "Type PUSH to continue"
if ($ok2 -eq "PUSH") {
    git push --force --set-upstream origin main
    if ($LASTEXITCODE -eq 0) {
        Write-Host ""
        Write-Host "Done. Repo is clean." -ForegroundColor Green
    } else {
        Write-Host "Push failed - check your GitHub credentials." -ForegroundColor Red
    }
} else {
    Write-Host "Not pushed. Files are ready at: $WorkDir"
    Write-Host "Push manually with: git push --force --set-upstream origin main"
}

Write-Host ""
Write-Host "STILL TO DO:" -ForegroundColor Yellow
Write-Host "  1. Make the repo PRIVATE on GitHub (Settings > Danger Zone)"
Write-Host "  2. Rotate the MailTester key"
Write-Host "  3. Apply the 3 patches in ENGINE-PATCHES.md"
Write-Host ""
Write-Host "Your data is safe on the Desktop in email_engine_output\" -ForegroundColor Green
Write-Host ""
