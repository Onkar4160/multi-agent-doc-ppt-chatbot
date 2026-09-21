# scripts/run_all.ps1
# Starts the FastAPI backend (uvicorn) and Streamlit UI in two separate terminal windows.
# Run from the project root:  .\scripts\run_all.ps1

$Root = Resolve-Path "$PSScriptRoot\.."

# ── Backend ────────────────────────────────────────────────────────────────────
Start-Process powershell -ArgumentList @(
    "-NoExit",
    "-Command",
    "cd '$Root'; .\.venv\Scripts\Activate.ps1; uvicorn app.main:app --reload --host 127.0.0.1 --port 8000"
) -WindowStyle Normal

Start-Sleep -Seconds 2   # give uvicorn a moment to bind

# ── Streamlit UI ───────────────────────────────────────────────────────────────
Start-Process powershell -ArgumentList @(
    "-NoExit",
    "-Command",
    "cd '$Root'; .\.venv\Scripts\Activate.ps1; streamlit run ui\streamlit_app.py --server.port 8501 --server.address 127.0.0.1"
) -WindowStyle Normal

Write-Host ""
Write-Host "======================================" -ForegroundColor Cyan
Write-Host "  Backend  : http://127.0.0.1:8000   " -ForegroundColor Green
Write-Host "  API docs : http://127.0.0.1:8000/docs" -ForegroundColor Green
Write-Host "  UI       : http://127.0.0.1:8501   " -ForegroundColor Yellow
Write-Host "======================================" -ForegroundColor Cyan
Write-Host "Both windows opened. Press Ctrl+C in each to stop." -ForegroundColor Gray
