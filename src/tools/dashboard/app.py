import sys
from pathlib import Path
from flask import Flask, jsonify, render_template, Response

# Garante que o importe do projeto raiz funciona
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from src.config import load_settings
from src.tools.status import (
    _create_session,
    _fetch_wallet_balance,
    _fetch_positions,
    _fetch_open_orders,
    _fetch_closed_pnl,
)
from src.tools.dashboard.process_manager import bot_manager

app = Flask(__name__)

# Inicialização Global da Sessão da Bybit
try:
    settings = load_settings()
    bybit_session = _create_session(settings)
except Exception as exc:
    print(f"Erro fatal ao carregar settings: {exc}")
    sys.exit(1)


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/status")
def api_status():
    """Retorna um JSON unificado com os dados financeiros e status das ordens."""
    wallet = _fetch_wallet_balance(bybit_session)
    positions = _fetch_positions(bybit_session)
    open_orders = _fetch_open_orders(bybit_session)
    history = _fetch_closed_pnl(bybit_session, limit=100)

    # Aninhar as ordens "reduceOnly" (TPs) dentro de suas respectivas posições para facilitar no Frontend
    for pos in positions:
        pos_symbol = pos["symbol"]
        pos["tps"] = []
        for order in open_orders:
            if order["symbol"] == pos_symbol and order.get("reduce_only"):
                pos["tps"].append(order)
        # Ordenar os TPs do menor para o maior preço (considerando que LONG sobe, e SHORT desce)
        # Simplificando, ordena pelo preço puro absoluto.
        pos["tps"].sort(key=lambda x: float(x.get("price", "0") or "0"))

    return jsonify({
        "wallet": wallet,
        "positions": positions,
        "history": history,
    })


@app.route("/api/bot")
def api_bot_status():
    """Retorna se o processo do bot (src.main) está ativo."""
    is_internal = bot_manager.is_running()
    is_external = False
    
    if not is_internal:
        is_external = bot_manager.check_external_process()

    return jsonify({
        "running": is_internal,
        "external": is_external,
        "testnet": settings.bybit_testnet,
        "live_execution": settings.enable_order_execution
    })


@app.route("/api/bot/start", methods=["POST"])
def api_bot_start():
    success, msg = bot_manager.start()
    return jsonify({"success": success, "message": msg})


@app.route("/api/bot/stop", methods=["POST"])
def api_bot_stop():
    success, msg = bot_manager.stop()
    return jsonify({"success": success, "message": msg})


@app.route("/api/panic", methods=["POST"])
def api_panic():
    """Botão de Pânico: Interrompe o robô, cancela todas as ordens e fecha todas as posições abertas linear."""
    messages = []
    
    # 1. Tentar forçar o fechamento do Bot para não gerar novas posições pós-pânico
    stopped, msg = bot_manager.stop()
    if stopped:
        messages.append("Bot interno paralisado.")
    elif bot_manager.check_external_process():
        messages.append("AVISO: Bot parece estar rodando em terminal externo. Recomendado fechar manualmente também.")
        
    # 2. Cancelar todas as ordens (incluindo SL/TP)
    try:
        resp = bybit_session.cancel_all_orders(category="linear")
        messages.append("Todas as ordens/TPs/SLs pendentes foram canceladas.")
    except Exception as exc:
        messages.append(f"ERRO ao cancelar ordens: {exc}")

    # 3. Mapear Posições Abertas e Emitir Liquidação a Mercado (reduceOnly)
    try:
        pos_list = _fetch_positions(bybit_session)
        closed_count = 0
        for pos in pos_list:
            size_val = float(pos.get("size", "0"))
            if size_val > 0:
                side = pos.get("side")
                symbol = pos.get("symbol")
                opposite_side = "Sell" if side == "Buy" else "Buy"
                
                try:
                    bybit_session.place_order(
                        category="linear",
                        symbol=symbol,
                        side=opposite_side,
                        orderType="Market",
                        qty=str(pos["size"]),
                        reduceOnly=True,
                        positionIdx=pos.get("positionIdx", 0),
                        timeInForce="IOC"
                    )
                    closed_count += 1
                except Exception as e_pos:
                    messages.append(f"ERRO ao tentar fechar posição de {symbol}: {e_pos}")

        if closed_count > 0:
            messages.append(f"{closed_count} posições abertas foram liquidadas a mercado com sucesso.")
        else:
            messages.append("Nenhuma posição aberta encontrada para liquidação.")
    except Exception as exc:
        messages.append(f"ERRO ao pesquisar posições para encerramento: {exc}")

    return jsonify({"success": True, "message": " | ".join(messages)})


@app.route("/api/position/close", methods=["POST"])
def api_position_close():
    """Fecha a mercado uma posição específica e cancela ordens pendentes desse ativo."""
    from flask import request
    data = request.json or {}
    symbol = data.get("symbol")
    
    if not symbol:
        return jsonify({"success": False, "message": "Symbol não fornecido."})
        
    messages = []
    
    # 1. Cancelar apenas ordens deste symbol
    try:
        bybit_session.cancel_all_orders(category="linear", symbol=symbol)
        messages.append(f"Ordens de {symbol} canceladas.")
    except Exception as exc:
        # Se não houver ordens abertas, a Bybit costuma lançar erro. Apenas ignora/loga.
        pass

    # 2. Localizar a posição para inverter e fechar
    try:
        pos_list = _fetch_positions(bybit_session)
        closed = False
        for pos in pos_list:
            if pos.get("symbol") == symbol:
                size_val = float(pos.get("size", "0"))
                if size_val > 0:
                    side = pos.get("side")
                    opposite_side = "Sell" if side == "Buy" else "Buy"
                    
                    try:
                        bybit_session.place_order(
                            category="linear",
                            symbol=symbol,
                            side=opposite_side,
                            orderType="Market",
                            qty=str(pos["size"]),
                            reduceOnly=True,
                            positionIdx=pos.get("positionIdx", 0),
                            timeInForce="IOC"
                        )
                        closed = True
                        messages.append(f"Posição de {symbol} liquidada a mercado com sucesso.")
                    except Exception as e_pos:
                        return jsonify({"success": False, "message": f"ERRO ao faturar mercado: {e_pos}"})
        
        if not closed:
            messages.append(f"Nenhuma posição ativa detectada para {symbol}.")
            
    except Exception as exc:
        return jsonify({"success": False, "message": f"Erro listando posição: {exc}"})

    return jsonify({"success": True, "message": " | ".join(messages)})


@app.route("/api/logs/stream")
def api_logs_stream():
    """Endpoint SSE (Server-Sent Events) para transmitir os logs do bot ao vivo."""
    def event_stream():
        q = bot_manager.subscribe_logs()
        try:
            while True:
                # Bloqueia até ter uma nova linha
                line = q.get()
                # Formato padrão SSE: "data: <mensagem>\n\n"
                yield f"data: {line}\n\n"
        except GeneratorExit:
            # Cliente fechou a conexão (tab fechada)
            bot_manager.unsubscribe_logs(q)
            
    return Response(event_stream(), mimetype="text/event-stream")


@app.route("/api/journals")
def api_journals():
    import glob
    import os
    import json
    
    project_root = Path(__file__).resolve().parents[3]
    journal_dir = project_root / "runtime" / "journal"
    
    files = glob.glob(str(journal_dir / "*.json"))
    files.sort(key=os.path.getmtime, reverse=True)
    
    journals = []
    for f in files[:100]:  # Limit to last 100 entries for performance
        try:
            with open(f, "r", encoding="utf-8") as file:
                journals.append(json.load(file))
        except Exception:
            continue
            
    return jsonify({"journals": journals})


if __name__ == "__main__":
    print("Iniciando Dashboard na porta 8050...")
    app.run(host="0.0.0.0", port=8050, debug=False, threaded=True)
