# MT5 Windows agent

The platform backend stays on Linux. This small process runs beside the MT5 terminal on Windows and makes outbound HTTPS requests to the deployed platform. It sends candle data for enabled strategies assigned to its MT5 account and executes only protected commands created by the server risk manager.

## Install

1. Install the MT5 desktop terminal and Python 3.11 or newer on the Windows machine. Sign in to the broker's **demo** account in MT5 and leave the terminal open.
2. Copy the `backend` directory to the Windows machine.
3. In PowerShell, from that directory, install dependencies:

   ```powershell
   py -m pip install -r requirements-mt5-windows.txt
   ```

4. Set the platform URL and the one-time agent token generated on the Broker Accounts page. Use the root HTTPS origin, without a trailing path:

   ```powershell
   [Environment]::SetEnvironmentVariable("TRADING_PLATFORM_URL", "https://trading.example.com", "User")
   [Environment]::SetEnvironmentVariable("TRADING_PLATFORM_AGENT_TOKEN", "PASTE_TOKEN_HERE", "User")
   ```

   Close and reopen PowerShell after setting these values. The token is a credential: do not place it in source control or share it.

5. From the copied `backend` directory, start the agent:

   ```powershell
   py -m mt5_agent.agent
   ```

The agent must run as the same Windows user that is logged into MT5. Keep both the terminal and agent running. For automatic startup, create a Windows Task Scheduler task triggered **At log on**, run it as that same user, and use `py -m mt5_agent.agent` with the copied `backend` directory as the working directory. The agent needs outbound HTTPS access; no inbound firewall port is required.

## Safety and recovery

- New agent accounts and tokens begin disabled for live orders. The agent locally refuses execution on a real account unless the server explicitly grants live permission; this deployment does not expose such a grant.
- Server order commands are durable and idempotent. The local SQLite journal is stored under `%USERPROFILE%\.trading-platform`. If the agent stops after an order submission begins, it reports an unknown result and does not submit the same command again.
- Keep the journal file when restarting or upgrading the agent. Deleting it removes the local duplicate-submission protection.
- MT5 terminal/agent downtime pauses that account's market data and prevents execution. The platform continues running strategies for other configured broker accounts.
- Automated execution remains blocked unless the server's global live-trading settings permit it. Validate the complete flow using a demo account first.
