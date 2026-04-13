"""CLI Status Tool — consulta saldo, posições e ordens na Bybit.

Processo independente do bot. Usa as mesmas credenciais do .env.
Referências oficiais:
  - Wallet Balance: https://bybit-exchange.github.io/docs/v5/account/wallet-balance
  - Positions: https://bybit-exchange.github.io/docs/v5/position
  - Open Orders: https://bybit-exchange.github.io/docs/v5/order/open-order
  - Closed PnL: https://bybit-exchange.github.io/docs/v5/position/close-pnl
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

# Garante que o import de src.config funcione quando executado como módulo
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.config import load_settings


def _create_session(settings):
    """Cria sessão pybit HTTP autenticada."""
    from pybit.unified_trading import HTTP

    return HTTP(
        api_key=settings.bybit_api_key,
        api_secret=settings.bybit_api_secret,
        testnet=settings.bybit_testnet,
    )


def _fetch_wallet_balance(session) -> dict:
    """GET /v5/account/wallet-balance — accountType=UNIFIED."""
    response = session.get_wallet_balance(accountType="UNIFIED")
    if response.get("retCode") != 0:
        return {"error": response.get("retMsg", "unknown")}

    accounts = response.get("result", {}).get("list", [])
    if not accounts:
        return {"error": "Nenhuma conta encontrada"}

    account = accounts[0]
    coins = account.get("coin", [])
    usdt = next((c for c in coins if c.get("coin") == "USDT"), None)

    total_margin_balance = float(account.get("totalMarginBalance", "0") or "0")
    total_initial_margin = float(account.get("totalInitialMargin", "0") or "0")
    margin_used_percent = 0.0
    if total_margin_balance > 0:
        margin_used_percent = (total_initial_margin / total_margin_balance) * 100

    return {
        "total_equity": account.get("totalEquity", "0"),
        "total_wallet_balance": account.get("totalWalletBalance", "0"),
        "total_unrealised_pnl": account.get("totalPerpUPL", "0"),
        "total_available_balance": account.get("totalAvailableBalance", "0"),
        "usdt_wallet_balance": usdt.get("walletBalance", "0") if usdt else "0",
        "usdt_available": usdt.get("availableToWithdraw", "0") if usdt else "0",
        "total_initial_margin": str(total_initial_margin),
        "total_margin_balance": str(total_margin_balance),
        "margin_used_percent": f"{margin_used_percent:.2f}",
    }


def _fetch_positions(session) -> list[dict]:
    """GET /v5/position/list — posições abertas em linear."""
    response = session.get_positions(category="linear", settleCoin="USDT")
    if response.get("retCode") != 0:
        return []

    positions = response.get("result", {}).get("list", [])
    active = []
    for p in positions:
        size = p.get("size", "0")
        if float(size) == 0:
            continue
        size_f = float(size)
        entry_str = p.get("avgPrice", "0") or "0"
        sl_str = p.get("stopLoss", "0") or "0"
        
        entry_f = float(entry_str)
        sl_f = float(sl_str)
        side = p.get("side", "")
        
        position_risk_usdt = 0.0
        if sl_f > 0 and size_f > 0:
            if side == "Buy":
                position_risk_usdt = max(0.0, (entry_f - sl_f) * size_f)
            else:
                position_risk_usdt = max(0.0, (sl_f - entry_f) * size_f)

        active.append({
            "symbol": p.get("symbol", ""),
            "side": side,
            "size": size,
            "leverage": p.get("leverage", ""),
            "entry_price": entry_str,
            "mark_price": p.get("markPrice", ""),
            "unrealised_pnl": p.get("unrealisedPnl", "0"),
            "stop_loss": sl_str,
            "take_profit": p.get("takeProfit", ""),
            "liq_price": p.get("liqPrice", ""),
            "position_value": p.get("positionValue", ""),
            "created_time": p.get("createdTime", ""),
            "position_risk_usdt": f"{position_risk_usdt:.2f}",
        })
    return active


def _fetch_open_orders(session, symbol: str | None = None) -> list[dict]:
    """GET /v5/order/realtime — ordens abertas."""
    kwargs = {"category": "linear", "limit": 50, "settleCoin": "USDT"}
    if symbol:
        kwargs["symbol"] = symbol
    response = session.get_open_orders(**kwargs)
    if response.get("retCode") != 0:
        return []

    orders = response.get("result", {}).get("list", [])
    result = []
    for o in orders:
        result.append({
            "symbol": o.get("symbol", ""),
            "side": o.get("side", ""),
            "order_type": o.get("orderType", ""),
            "price": o.get("price", ""),
            "qty": o.get("qty", ""),
            "order_status": o.get("orderStatus", ""),
            "order_link_id": o.get("orderLinkId", ""),
            "order_id": o.get("orderId", ""),
            "reduce_only": o.get("reduceOnly", False),
            "created_time": o.get("createdTime", ""),
        })
    return result


def _fetch_closed_pnl(session, limit: int = 10) -> list[dict]:
    """GET /v5/position/closed-pnl — PnL de posições fechadas."""
    response = session.get_closed_pnl(category="linear", limit=limit)
    if response.get("retCode") != 0:
        return []

    records = response.get("result", {}).get("list", [])
    result = []
    for r in records:
        result.append({
            "symbol": r.get("symbol", ""),
            "side": r.get("side", ""),
            "qty": r.get("qty", ""),
            "entry_price": r.get("avgEntryPrice", ""),
            "exit_price": r.get("avgExitPrice", ""),
            "closed_pnl": r.get("closedPnl", "0"),
            "leverage": r.get("leverage", ""),
            "created_time": r.get("createdTime", ""),
            "updated_time": r.get("updatedTime", ""),
        })
    return result


def _format_number(value: str, decimals: int = 2) -> str:
    """Formata número string para exibição."""
    try:
        num = float(value)
        return f"{num:,.{decimals}f}"
    except (ValueError, TypeError):
        return value or "N/A"


def _format_pnl(value: str) -> str:
    """Formata PnL com sinal e cor ANSI."""
    try:
        num = float(value)
        color = "\033[92m" if num >= 0 else "\033[91m"  # verde / vermelho
        sign = "+" if num >= 0 else ""
        return f"{color}{sign}{num:,.2f}\033[0m"
    except (ValueError, TypeError):
        return value or "N/A"


def _classify_order(order_link_id: str) -> str:
    """Classifica ordem pelo orderLinkId do bot."""
    if order_link_id.startswith("tp"):
        parts = order_link_id.split("-")
        tp_num = parts[0].replace("tp", "TP")
        return tp_num
    if order_link_id.startswith("entry-"):
        return "Entry"
    return "Manual"


def _print_header(env_mode: str, testnet: bool):
    net = "TESTNET" if testnet else "MAINNET"
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    print()
    print("\033[96m╔══════════════════════════════════════════════════════════════╗\033[0m")
    print(f"\033[96m║\033[0m  \033[1mBYBIT TRADE BOT — STATUS\033[0m                                    \033[96m║\033[0m")
    print(f"\033[96m║\033[0m  {net} | {timestamp}              \033[96m║\033[0m")
    print("\033[96m╚══════════════════════════════════════════════════════════════╝\033[0m")


def _print_wallet(wallet: dict):
    if "error" in wallet:
        print(f"\n\033[91m  ❌ Erro ao consultar saldo: {wallet['error']}\033[0m")
        return

    print()
    print("\033[93m  ── SALDO DA CONTA ──────────────────────────────────────────\033[0m")
    print(f"  Equity Total:       \033[1m${_format_number(wallet['total_equity'])}\033[0m")
    print(f"  Saldo Carteira:     ${_format_number(wallet['total_wallet_balance'])}")
    print(f"  P&L Não Realizado:  {_format_pnl(wallet['total_unrealised_pnl'])}")
    print(f"  Disponível:         ${_format_number(wallet['total_available_balance'])}")
    print(f"  USDT Disponível:    ${_format_number(wallet['usdt_available'])}")


def _print_positions(positions: list[dict], open_orders: list[dict]):
    print()
    if not positions:
        print("\033[93m  ── POSIÇÕES ABERTAS ────────────────────────────────────────\033[0m")
        print("  Nenhuma posição aberta.")
        return

    print(f"\033[93m  ── POSIÇÕES ABERTAS ({len(positions)}) ──────────────────────────────────\033[0m")
    for pos in positions:
        symbol = pos["symbol"]
        side = pos["side"]
        side_color = "\033[92m" if side == "Buy" else "\033[91m"
        side_label = "LONG" if side == "Buy" else "SHORT"

        print()
        print(f"  {side_color}● {symbol} | {side_label} | {pos['leverage']}x\033[0m")
        print(f"    Entrada: {_format_number(pos['entry_price'], 4)}  |  Mark: {_format_number(pos['mark_price'], 4)}  |  Qty: {pos['size']}")
        print(f"    P&L: {_format_pnl(pos['unrealised_pnl'])}  |  Valor: ${_format_number(pos['position_value'])}")

        sl = pos.get("stop_loss", "")
        liq = pos.get("liq_price", "")
        if sl and sl != "0":
            print(f"    SL: {_format_number(sl, 4)}", end="")
        else:
            print("    SL: \033[91mNão configurado\033[0m", end="")
        if liq and liq != "0":
            print(f"  |  Liquidação: {_format_number(liq, 4)}")
        else:
            print()

        # Buscar TPs pendentes para este symbol
        symbol_orders = [o for o in open_orders if o["symbol"] == symbol and o["reduce_only"]]
        if symbol_orders:
            print("    TPs:")
            # Ordenar por preço
            symbol_orders.sort(key=lambda o: float(o.get("price", "0") or "0"))
            for order in symbol_orders:
                status = order["order_status"]
                price = _format_number(order["price"], 4)
                qty = order["qty"]
                label = _classify_order(order.get("order_link_id", ""))
                if status == "Filled":
                    print(f"      \033[92m✅ {label}: {price} (Filled) qty={qty}\033[0m")
                elif status == "PartiallyFilled":
                    print(f"      \033[93m⚡ {label}: {price} (Parcial) qty={qty}\033[0m")
                else:
                    print(f"      ⏳ {label}: {price} ({status}) qty={qty}")
        else:
            print("    TPs: Nenhuma ordem reduceOnly pendente")


def _print_closed_pnl(records: list[dict]):
    print()
    if not records:
        print("\033[93m  ── HISTÓRICO RECENTE ───────────────────────────────────────\033[0m")
        print("  Nenhum trade fechado recentemente.")
        return

    print(f"\033[93m  ── HISTÓRICO RECENTE ({len(records)}) ─────────────────────────────────\033[0m")
    for r in records:
        symbol = r["symbol"]
        side = r["side"]
        side_label = "LONG" if side == "Buy" else "SHORT"
        pnl = _format_pnl(r["closed_pnl"])
        entry = _format_number(r["entry_price"], 4)
        exit_p = _format_number(r["exit_price"], 4)
        lev = r.get("leverage", "?")
        print(f"  {symbol} {side_label} {lev}x | Entrada: {entry} → Saída: {exit_p} | P&L: {pnl}")
    print()


def main():
    try:
        settings = load_settings()
    except Exception as exc:
        print(f"\033[91mErro ao carregar settings: {exc}\033[0m")
        sys.exit(1)

    session = _create_session(settings)
    _print_header(settings.env, settings.bybit_testnet)

    wallet = _fetch_wallet_balance(session)
    _print_wallet(wallet)

    positions = _fetch_positions(session)
    open_orders = _fetch_open_orders(session)
    _print_positions(positions, open_orders)

    closed = _fetch_closed_pnl(session, limit=5)
    _print_closed_pnl(closed)

    print("\033[90m  Pressione qualquer tecla para sair...\033[0m")


if __name__ == "__main__":
    main()
