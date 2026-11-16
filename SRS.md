Software Requirements Specification (SRS) & Technical Quotation
AI-Powered Forex & Multi-Asset Trading System
Version: v1.0 (Production Scope)
Currency: Ethiopian Birr (ETB)
Estimated Timeline: 4–5 Weeks


1. Executive Summary & Project Objective
This document outlines the software requirements, system design, and investment costs for an institutional-grade, AI-Powered Automated Trading Platform built for private client use.

The system is engineered to eliminate emotional bias and manual fatigue. Operating 24/7 on a secure cloud server, the platform continuously analyzes real-time market data across Forex, Gold, and Crypto against predefined quantitative and technical rules. Trades can be executed either 100% autonomously (Full Auto-Pilot) or upon one-tap mobile confirmation (Semi-Automated).

Strategic Growth Pathway: Although engineered today as a dedicated private terminal for one client, the underlying engine is modular and containerized, enabling seamless future expansion into a multi-subscriber commercial SaaS platform without rewriting core trading logic [cite: 1].


2. High-Level Architecture & Core Modules
The solution connects a high-performance Python trading engine with two complementary client interfaces:

Telegram Bot: Instant push alerts, 1-tap trade approval & emergency Kill-Switch [cite: 1]
Core Trading Engine: FastAPI 24/7 runner, AI strategy compiler & Risk Guardrails [cite: 1]
Broker Connector: Direct execution via MT5 API, cTrader, or Crypto (CCXT) [cite: 1]
Module 1: AI Strategy & Automated Execution Engine
Natural-Language Rule Input: The client can express strategies in plain English (e.g., "Buy EUR/USD when 15m RSI < 30 and 9 EMA crosses above 21 EMA. Risk 1% of balance. SL 20 pips, TP 40 pips").
Deterministic Execution (0% Hallucination): An AI compiler strictly translates prompts into mathematical rules with validated parameters before passing them to the execution engine [cite: 1].
24/7 Autonomous Scanning: Continuous background monitoring of market ticks and candles, firing orders instantaneously upon condition fulfillment [cite: 1].
Historical Backtesting: Ability to test strategy rules against past historical data to confirm win-rate and drawdown metrics before committing real capital [cite: 1].
Module 2: Telegram Mobile Controller Bot
Real-time Trade Alerts: Instant notifications detailing entry price, position size, risk amount, and SL/TP targets [cite: 2].
One-Tap Order Confirmation: In semi-automated mode, the bot presents a chart snapshot with [Approve Trade] and [Skip] buttons [cite: 2].
Emergency Kill-Switch: A dedicated [Close All Positions] button closes all open market orders and pauses bot execution instantly [cite: 2].
Daily Performance Digest: Automated P&L summary and account equity report delivered every evening [cite: 2].
Module 3: Web Dashboard & Live Chart Terminal
TradingView Lightweight Charts: High-performance, canvas-based live candlestick charts supporting multi-timeframe analysis (1m, 5m, 15m, 1h, 4h, 1D) [cite: 2].
Technical Indicator Overlays: Live plotting of RSI, Moving Averages (EMA/SMA), MACD, and Bollinger Bands [cite: 2].
Account Equity Curve: Graphical visualizer tracking account capital growth and trade history [cite: 2].
Module 4: Risk Management System (RMS) - Hard Capital Protection
Mandatory Stop-Loss: The engine automatically rejects any trade attempt lacking a valid Stop-Loss [cite: 3].
Daily Drawdown Circuit Breaker: If daily account losses reach a pre-set ceiling (e.g., 3% of capital), the bot terminates all active trading for the day [cite: 3].
High-Impact News Filter: Pauses trading 15 minutes before and after major macroeconomic data releases (e.g., US NFP, CPI, Fed decisions) [cite: 3].
Module 5: Broker & Liquidity Integration
Forex & Gold (XAUUSD): Native direct connectivity via MetaTrader 5 (MT5) Python API or cTrader Open API [cite: 3].
Crypto (BTC, ETH, Altcoins): Low-latency REST/WebSocket connections to Binance, Bybit, or OKX using the CCXT library [cite: 3].


3. Technology Stack
Layer
Selected Technology
Key Benefit
Frontend Terminal
Next.js 15 (React 19) + Tailwind CSS
Fast, responsive dark-mode trading interface [cite: 3].
Charting Engine
TradingView Lightweight Charts
Free & open-source (0 license fees), zero lag [cite: 3].
Mobile Bot
python-telegram-bot
Native mobile push alerts and interactive buttons [cite: 3].
Core Trading API
Python 3.12 (FastAPI)
High-speed async API, native MT5 and quant packages [cite: 3].
Database & Cache
PostgreSQL + Redis
Persistent trade logs with sub-millisecond price caching [cite: 3].
Cloud Infrastructure
Cloud VPS (Ubuntu Linux / Docker)
99.9% uptime, 24/7 uninterrupted cloud operation [cite: 3].



4. Implementation Timeline
Phase
Deliverables
Duration
Phase 1
Broker connection setup (MT5 / Crypto), live price streaming, Telegram bot interface
1–2 Weeks [cite: 4]
Phase 2
AI rule engine, 24/7 automated execution loop, Stop-Loss & daily loss safeguards
1–2 Weeks [cite: 4]
Phase 3
Web dashboard with live TradingView charts, backtesting module, and analytics
1 Week [cite: 4]
Phase 4
Live account paper testing, cloud VPS deployment, and final handover
1 Week [cite: 4]
Total Timeline
Turnkey Production Delivery
4–5 Weeks [cite: 4]



5. Development & Deployment Pricing (in Ethiopian Birr - ETB)
Option 1: Standard AI Bot Package — 150,000 ETB Total
Includes: Telegram Mobile Bot + Broker API Connector + 24/7 AI Automated Trading + Risk Guardrails (Stop-Loss & Drawdown Breaker).

No.
Service Item
Price (ETB)
1
System Development Price
130,000 ETB [cite: 4]
2
Server Deployment & Configuration Price
20,000 ETB [cite: 4]
Total
Standard Package Total Investment
150,000 ETB [cite: 4]

Option 2: Full Pro Suite Package — 200,000 ETB Total
Includes: Everything in Standard Package + Dedicated Web TradingView Chart Terminal + Strategy Studio + Visual Portfolio Analytics.

No.
Service Item
Price (ETB)
1
System Development Price
175,000 ETB [cite: 5]
2
Server Deployment & Configuration Price
25,000 ETB [cite: 5]
Total
Full Pro Suite Total Investment
200,000 ETB [cite: 5]



6. Monthly Operating Costs (OpEx)
Direct third-party hosting and AI token costs to maintain 24/7 cloud execution:

Service Item
Provider
Estimated Cost (ETB)
Cloud VPS Hosting (4–8 GB RAM)
Hetzner / DigitalOcean
~2,500–3,500 ETB / month [cite: 5]
AI API Usage (LLM Tokens)
OpenAI / Anthropic / Gemini
~1,200–2,000 ETB / month [cite: 5]
Total
Total Monthly Cloud & AI Running Cost
~3,700–5,500 ETB / month [cite: 5]



7. Client Onboarding Checklist (Next Steps)
To initiate development sprints, the client needs to confirm:

Target Broker / Exchange: (e.g., MetaTrader 5 broker, cTrader, or Binance/Bybit) [cite: 5].
Initial Strategy Rules: The exact indicators or conditions to automate first [cite: 5].
Execution Preference: Full Auto-Pilot (hands-free) or Semi-Automated (one-tap confirmation) [cite: 5].


8. Legal & Technology Disclaimer
Technological Execution Tool: This platform is strictly an automated execution and algorithmic assistance tool built to user specifications. It does not provide financial, investment, or legal advice [cite: 5].
Non-Custodial / Zero Fund Access: The platform connects to the client's private broker account via trade-only API permissions. The system possesses no capability or authorization to withdraw or transfer client funds [cite: 5].
Market Risk Acknowledgment: Trading financial instruments (Forex, Gold, Cryptocurrencies) carries inherent market volatility and capital risk [cite: 5].
