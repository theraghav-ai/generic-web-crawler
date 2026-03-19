<#
.SYNOPSIS
    Management script for the Generic Web Crawler (Windows PowerShell).
.DESCRIPTION
    Provides commands for setup, crawling, status checking, and log viewing.
.EXAMPLE
    .\manage.ps1 setup
    .\manage.ps1 crawl https://www.python.org/ --max-pages 20 --max-depth 2
    .\manage.ps1 status https://www.python.org/
    .\manage.ps1 logs
#>

param(
    [Parameter(Position = 0)]
    [string]$Command = "help",

    [Parameter(Position = 1, ValueFromRemainingArguments = $true)]
    [string[]]$Arguments
)

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Definition

function Invoke-Setup {
    Write-Host "Installing dependencies..." -ForegroundColor Cyan
    python -m pip install --upgrade pip
    pip install -r "$ScriptDir\requirements.txt"
    Write-Host "Installing Playwright Chromium..." -ForegroundColor Cyan
    python -m playwright install chromium
    Write-Host "Setup complete." -ForegroundColor Green
}

function Invoke-Crawl {
    param([string[]]$CrawlArgs)
    Push-Location $ScriptDir
    try {
        python main.py crawl @CrawlArgs
    }
    finally {
        Pop-Location
    }
}

function Invoke-Status {
    param([string[]]$StatusArgs)
    Push-Location $ScriptDir
    try {
        python main.py status @StatusArgs
    }
    finally {
        Pop-Location
    }
}

function Invoke-Logs {
    $logFile = Join-Path $ScriptDir "logs\crawler.log"
    if (Test-Path $logFile) {
        Get-Content $logFile -Tail 100 -Wait
    }
    else {
        Write-Host "Log file not found: $logFile" -ForegroundColor Yellow
    }
}

function Show-Help {
    Write-Host @"
Usage: .\manage.ps1 <command> [args]

Commands:
  setup                      Install dependencies and Playwright Chromium
  crawl <url> [options]      Run a crawl for the target website
  status [url]               Show the last-run metadata
  logs                       Tail the crawler log
  help                       Show this help

Examples:
  .\manage.ps1 setup
  .\manage.ps1 crawl https://www.python.org/ --max-pages 40 --max-depth 2
  .\manage.ps1 status https://www.python.org/
"@
}

switch ($Command) {
    "setup"  { Invoke-Setup }
    "crawl"  { Invoke-Crawl -CrawlArgs $Arguments }
    "status" { Invoke-Status -StatusArgs $Arguments }
    "logs"   { Invoke-Logs }
    "help"   { Show-Help }
    default  {
        Write-Host "Unknown command: $Command" -ForegroundColor Red
        Show-Help
        exit 1
    }
}
