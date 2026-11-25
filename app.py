import streamlit as st
import asyncio
import aiohttp
import ccxt.async_support as ccxt
import pandas as pd
from datetime import datetime

st.set_page_config(page_title="Arbitragem Pro: OKX x Bybit", layout="wide")

if "logs" not in st.session_state:
    st.session_state.logs = []
if "rodando" not in st.session_state:
    st.session_state.rodando = False
if "saldos" not in st.session_state:
    st.session_state.saldos = {"okx_usdt": 0.0, "okx_btc": 0.0, "byb_usdt": 0.0, "byb_btc": 0.0}

st.title("Arbitragem Spot Pro: OKX ↔ Bybit (BTC/USDT)")

with st.sidebar:
    st.header("1. Controle Operacional")
    modo_simulacao = st.toggle("Modo Simulação (Sem ordens reais)", value=True)
    capital_usdt = st.number_input("Capital por operação (USDT)", min_value=15.0, value=100.0, step=10.0)
    lucro_min_pct = st.number_input("Lucro Líquido Mínimo (%)", min_value=0.01, value=0.08, step=0.01, format="%.2f")
    intervalo_seg = st.slider("Intervalo de leitura (seg)", min_value=2.0, max_value=15.0, value=4.0, step=0.5)

    st.divider()
    st.header("2. Taxas Taker (%)")
    taxa_okx = st.number_input("Taxa OKX (%)", value=0.10, step=0.01, format="%.2f") / 100
    taxa_bybit = st.number_input("Taxa Bybit (%)", value=0.10, step=0.01, format="%.2f") / 100

    st.divider()
    st.header("3. Alertas Telegram (Opcional)")
    tg_token = st.text_input("Bot Token", type="password", value=st.secrets.get("TG_TOKEN", ""))
    tg_chat_id = st.text_input("Chat ID", value=st.secrets.get("TG_CHAT_ID", ""))

    st.divider()
    st.header("4. Chaves API (Spot Trade)")
    okx_key = st.text_input("OKX API Key", type="password", value=st.secrets.get("OKX_KEY", ""))
    okx_secret = st.text_input("OKX Secret", type="password", value=st.secrets.get("OKX_SECRET", ""))
    okx_pass = st.text_input("OKX Passphrase", type="password", value=st.secrets.get("OKX_PASS", ""))
    byb_key = st.text_input("Bybit API Key", type="password", value=st.secrets.get("BYB_KEY", ""))
    byb_secret = st.text_input("Bybit Secret", type="password", value=st.secrets.get("BYB_SECRET", ""))

c1, c2 = st.columns(2)
if c1.button("▶ Iniciar Robô", type="primary", use_container_width=True):
    st.session_state.rodando = True
if c2.button("⏹ Parar", use_container_width=True):
    st.session_state.rodando = False

async def enviar_telegram(mensagem):
    if not tg_token or not tg_chat_id:
        return
    url = f"https://telegram.org{tg_token}/sendMessage"
    payload = {"chat_id": tg_chat_id, "text": mensagem, "parse_mode": "HTML"}
    try:
        async with aiohttp.ClientSession() as session:
            await session.post(url, json=payload, timeout=5)
    except Exception:
        pass

async def buscar_precos_diretos():
    async with aiohttp.ClientSession() as session:
        url_okx = "https://okx.com"
        url_byb = "https://bybit.com"
        
        async with session.get(url_okx, timeout=5) as r1, session.get(url_byb, timeout=5) as r2:
            d_okx = await r1.json()
            d_byb = await r2.json()

            if d_okx.get("code") != "0":
                raise Exception(f"OKX bloqueou temporariamente: {d_okx.get('msg')}")
            if d_byb.get("retCode") != 0:
                raise Exception(f"Bybit bloqueou temporariamente: {d_byb.get('retMsg')}")

            t_okx = d_okx["data"]
            t_byb = d_byb["result"]["list"]

            return {
                "okx_ask": float(t_okx["askPx"]),
                "okx_ask_vol": float(t_okx["askSz"]),
                "okx_bid": float(t_okx["bidPx"]),
                "okx_bid_vol": float(t_okx["bidSz"]),
                "byb_ask": float(t_byb["ask1Price"]),
                "byb_ask_vol": float(t_byb["ask1Size"]),
                "byb_bid": float(t_byb["bid1Price"]),
                "byb_bid_vol": float(t_byb["bid1Size"]),
            }

def calcular_rota(preco_compra, vol_compra, tx_compra, preco_venda, vol_venda, tx_venda, capital):
    qtd_desejada = capital / preco_compra
    qtd_btc = min(qtd_desejada, vol_compra, vol_venda)
    if qtd_btc <= 0:
        return 0.0, 0.0, 0.0

    custo_usdt = qtd_btc * preco_compra
    btc_liq = qtd_btc * (1 - tx_compra)
    custo_taxa_compra_usdt = (qtd_btc - btc_liq) * preco_compra
    custo_total = custo_usdt + custo_taxa_compra_usdt

    receita_liq = (qtd_btc * preco_venda) * (1 - tx_venda)
    lucro_usdt = receita_liq - custo_total
    lucro_pct = (lucro_usdt / custo_total) * 100
    return round(qtd_btc, 6), lucro_usdt, lucro_pct

async def executar_com_trava_seguranca(qtd_btc, direcao, lucro_est):
    okx = ccxt.okx({
        'apiKey': okx_key, 'secret': okx_secret, 'password': okx_pass,
        'enableRateLimit': True, 'options': {'defaultType': 'spot', 'fetchMarkets': False}
    })
    bybit = ccxt.bybit({
        'apiKey': byb_key, 'secret': byb_secret,
        'enableRateLimit': True, 'options': {'defaultType': 'spot', 'fetchMarkets': False}
    })

    ex_compra = okx if direcao == "OKX → BYB" else bybit
    ex_venda = bybit if direcao == "OKX → BYB" else okx

    try:
        res_compra, res_venda = await asyncio.gather(
            ex_compra.create_market_buy_order('BTC/USDT', qtd_btc),
            ex_venda.create_market_sell_order('BTC/USDT', qtd_btc),
            return_exceptions=True
        )

        compra_ok = not isinstance(res_compra, Exception)
        venda_ok = not isinstance(res_venda, Exception)

        if compra_ok and venda_ok:
            msg = f"✅ <b>ARBITRAGEM EXECUTADA ({direcao})</b>\nQtd: {qtd_btc} BTC\nLucro Est.: ${lucro_est:.4f}"
            await enviar_telegram(msg)
            return "EXECUTADA COM SUCESSO"
        elif compra_ok and not venda_ok:
            try:
                qtd_liquida = round(qtd_btc * 0.9985, 6)
                await ex_compra.create_market_sell_order('BTC/USDT', qtd_liquida)
                acao = "Compra revertida a mercado."
            except Exception as e_rev:
                acao = f"FALHA REVERSÃO: {e_rev}"
            await enviar_telegram(f"🚨 <b>TRAVA ACIONADA ({direcao})</b>\nVenda falhou: {res_venda}\n{acao}")
            return f"TRAVA: {acao}"
        elif venda_ok and not compra_ok:
            try:
                await ex_venda.create_market_buy_order('BTC/USDT', qtd_btc)
                acao = "Venda recomprada a mercado."
            except Exception as e_rev:
                acao = f"FALHA RECOMPRA: {e_rev}"
            await enviar_telegram(f"🚨 <b>TRAVA ACIONADA ({direcao})</b>\nCompra falhou: {res_compra}\n{acao}")
            return f"TRAVA: {acao}"
        else:
            return f"ERRO AMBAS PONTAS: {res_compra} | {res_venda}"
    finally:
        await okx.close()
        await bybit.close()

async def ciclo_monitoramento():
    if okx_key and byb_key:
        okx = ccxt.okx({'apiKey': okx_key, 'secret': okx_secret, 'password': okx_pass, 'options': {'fetchMarkets': False}})
        bybit = ccxt.bybit({'apiKey': byb_key, 'secret': byb_secret, 'options': {'fetchMarkets': False}})
        try:
            bal_okx, bal_byb = await asyncio.gather(okx.fetch_balance(), bybit.fetch_balance())
            st.session_state.saldos = {
                "okx_usdt": float(bal_okx.get('USDT', {}).get('free', 0.0)),
                "okx_btc": float(bal_okx.get('BTC', {}).get('free', 0.0)),
                "byb_usdt": float(bal_byb.get('USDT', {}).get('free', 0.0)),
                "byb_btc": float(bal_byb.get('BTC', {}).get('free', 0.0)),
            }
        except Exception:
            pass
        finally:
            await okx.close()
            await bybit.close()

    precos = await buscar_precos_diretos()

    q1, l_usdt1, l_pct1 = calcular_rota(
        precos["okx_ask"], precos["okx_ask_vol"], taxa_okx,
        precos["byb_bid"], precos["byb_bid_vol"], taxa_bybit, capital_usdt
    )
    q2, l_usdt2, l_pct2 = calcular_rota(
        precos["byb_ask"], precos["byb_ask_vol"], taxa_bybit,
        precos["okx_bid"], precos["okx_bid_vol"], taxa_okx, capital_usdt
    )

    status_exec = "Monitorando"

    if l_pct1 >= lucro_min_pct:
        if modo_simulacao:
            status_exec = "SIMULAÇÃO: OKX → BYB"
            await enviar_telegram(f"🔔 <b>Oportunidade Simulada (OKX → Bybit)</b>\nLucro Líq.: {l_pct1:.4f}% (${l_usdt1:.4f})")
        elif okx_key and byb_key:
            if st.session_state.saldos["okx_usdt"] >= capital_usdt and st.session_state.saldos["byb_btc"] >= q1:
                status_exec = await executar_com_trava_seguranca(q1, "OKX → BYB", l_usdt1)
            else:
                status_exec = "BLOQUEADO: Saldo insuficiente"

    elif l_pct2 >= lucro_min_pct:
        if modo_simulacao:
            status_exec = "SIMULAÇÃO: BYB → OKX"
            await enviar_telegram(f"🔔 <b>Oportunidade Simulada (Bybit → OKX)</b>\nLucro Líq.: {l_pct2:.4f}% (${l_usdt2:.4f})")
        elif okx_key and byb_key:
            if st.session_state.saldos["byb_usdt"] >= capital_usdt and st.session_state.saldos["okx_btc"] >= q2:
                status_exec = await executar_com_trava_seguranca(q2, "BYB → OKX", l_usdt2)
            else:
                status_exec = "BLOQUEADO: Saldo insuficiente"

    return {
        "Hora": datetime.now().strftime("%H:%M:%S"),
        "OKX Ask": precos["okx_ask"],
        "Bybit Bid": precos["byb_bid"],
        "OKX→BYB (%)": round(l_pct1, 4),
        "OKX→BYB ($)": round(l_usdt1, 4),
        "Bybit Ask": precos["byb_ask"],
        "OKX Bid": precos["okx_bid"],
        "BYB→OKX (%)": round(l_pct2, 4),
        "BYB→OKX ($)": round(l_usdt2, 4),
        "Status": status_exec
    }

st.subheader("Saldos Disponíveis (Conta Trading)")
s = st.session_state.saldos
sc1, sc2, sc3, sc4 = st.columns(4)
sc1.metric("OKX — USDT Livre", f"${s['okx_usdt']:,.2f}")
sc2.metric("OKX — BTC Livre", f"{s['okx_btc']:.6f} BTC")
sc3.metric("Bybit — USDT Livre", f"${s['byb_usdt']:,.2f}")
sc4.metric("Bybit — BTC Livre", f"{s['byb_btc']:.6f} BTC")

st.divider()
