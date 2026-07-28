@echo off
REM One-click: start local Web UI + expose via Cloudflare Tunnel (free, no credit card)
cd /d "F:\code\project1"

REM Step 1: launch local Gradio service in background
start "WebUI" "C:\Python314\python.exe" web_ui.py

REM Step 2: wait for service to boot
timeout /t 10 >nul

REM Step 3: expose to public internet via NAMED Cloudflare Tunnel (stable url)
REM First-time setup (free Cloudflare account, no card):
REM   cloudflared tunnel login
REM   cloudflared tunnel create agent
REM This gives a STABLE url: https://<tunnel-id>.cfargotunnel.com (survives restarts)
cloudflared tunnel run --url http://localhost:7860 agent
