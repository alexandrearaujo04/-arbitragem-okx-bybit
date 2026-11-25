import streamlit as st
import asyncio
import aiohttp
import ccxt.async_support as ccxt
import pandas as pd
from datetime import datetime

st.set_page_config(page_title="Arbitragem OKX x Bybit", layout="wide")

if "logs" not in st.session_state:
    st.session_state.logs = []
if "rodando" not in st.session_state:
    st.session_state.rodando = False

st.title("Arbitragem Spot: OKX ↔ Bybit (BTC/USDT)")

with st.sidebar:
    modo_sim = st.toggle("Modo Simulação", value=True)
    capital = st.number_input("Capital (USDT)", min_value=15.0, value=100.0, step=10.0)
    lucro_min = st.number_input("Lucro Mínimo (%)", min_value=0.01, value=0.08, step=0.01, format="%.2f")
    intervalo = st.slider("Intervalo (seg)", 2.0, 15.0, 4.0, 0.5)
    tx_okx = st.number_input("Taxa OKX (%)", value=0.10, step=0.01, format="%.2f") / 100
    tx_byb = st.number_input("Taxa Bybit (%)", value=0.10, step=0.01, format="%.2f") / 100
    tg_token = st.text_input("Telegram Token", type="password", value=st.secrets.get("TG_TOKEN", ""))
    tg_chat = st.text_input("Telegram Chat ID", value=st.secrets.get("TG_CHAT_ID", ""))
    okx_k = st.text_input("OKX Key", type="password", value=st.secrets.get("OKX_KEY", ""))
    okx_s = st.text_input("OKX Secret", type="password", value=st.secrets.get("OKX_SECRET", ""))
    okx_p = st.text_input("OKX Passphrase", type="password", value=st.secrets.get("OKX_PASS", ""))
    byb_k = st.text_input("Bybit Key", type="password", value=st.secrets.get("BYB_KEY", ""))
    byb_s = st.text_input("Bybit Secret", type="password", value=st.secrets.get("BYB_SECRET", ""))

c1, c2 = st.columns(2)
if c1.button("▶ Iniciar Robô", type="primary", use_container_width=True):
    st.session_state.rodando = True
if c2.button("⏹ Parar", use_container_width=True):
    st.session_state.rodando = False

painel = st.empty()

async def avisar_tg(msg):
    if tg_token and tg_chat:
        url = f"https://telegram.org{tg_token}/sendMessage"
        async with aiohttp.ClientSession() as s:
            await s.post(url, json={"chat_id": tg_chat, "text": msg}, timeout=5)

def calc(p_comp, v_comp, t_comp, p_vend, v_vend, t_vend):
    qtd = min(capital / p_comp, v_comp, v_vend)
    if qtd <= 0:
        return 0.0, 0.0, 0.0
    custo = (qtd * p_comp) * (1 + t_comp)
    receita = (qtd * p_vend) * (1 - t_vend)
    lucro = receita - custo
    return round(qtd, 6), lucro, (lucro / custo) * 100

async def executar(qtd, dir_op, lucro_est):
    okx = ccxt.okx({'apiKey': okx_k, 'secret': okx_s, 'password': okx_p, 'hostname': '://okx.com', 'options': {'fetchMarkets': False}})
    byb = ccxt.bybit({'apiKey': byb_k, 'secret': byb_s, 'hostname': 'bytick.com', 'options': {'fetchMarkets': False}})
    ex_c = okx if dir_op == "OKX->BYB" else byb
    ex_v = byb if dir_op == "OKX->BYB" else okx
    rc, rv = await asyncio.gather(
        ex_c.create_market_buy_order('BTC/USDT', qtd),
        ex_v.create_market_sell_order('BTC/USDT', qtd),
        return_exceptions=True
    )
    ok_c, ok_v = not isinstance(rc, Exception), not isinstance(rv, Exception)
    if ok_c and not ok_v:
        await ex_c.create_market_sell_order('BTC/USDT', round(qtd * 0.9985, 6))
        status = f"TRAVA: Venda falhou, compra revertida ({rv})"
    elif ok_v and not ok_c:
        await ex_v.create_market_buy_order('BTC/USDT', qtd)
        status = f"TRAVA: Compra falhou, venda recomprada ({rc})"
    elif ok_c and ok_v:
        status = "EXECUTADA COM SUCESSO"
    else:
        status = f"ERRO: {rc} | {rv}"
    await okx.close()
    await byb.close()
    await avisar_tg(f"{dir_op} | {status} | Est: ${lucro_est:.4f}")
    return status

async def rodar_ciclo():
    headers = {"User-Agent": "Mozilla/5.0"}
    url_okx = "".join(["https://", "aws.", "://okx.com"])
    url_byb = "".join(["https://", "api.", "://bytick.com"])
    async with aiohttp.ClientSession(headers=headers) as s:
        r1 = await s.get(url_okx, timeout=8)
        r2 = await s.get(url_byb, timeout=8)
        d1 = await r1.json(content_type=None)
        d2 = await r2.json(content_type=None)
        t1 = d1["data"].pop(0)
        t2 = d2["result"]["list"].pop(0)

    o_ask, o_ask_v = float(t1["askPx"]), float(t1["askSz"])
    o_bid, o_bid_v = float(t1["bidPx"]), float(t1["bidSz"])
    b_ask, b_ask_v = float(t2["ask1Price"]), float(t2["ask1Size"])
    b_bid, b_bid_v = float(t2["bid1Price"]), float(t2["bid1Size"])

    q1, lu1, lp1 = calc(o_ask, o_ask_v, tx_okx, b_bid, b_bid_v, tx_byb)
    q2, lu2, lp2 = calc(b_ask, b_ask_v, tx_byb, o_bid, o_bid_v, tx_okx)

    st_op = "Monitorando"
    if lp1 >= lucro_min:
        st_op = "SIMULADO OKX->BYB" if modo_sim else await executar(q1, "OKX->BYB", lu1)
        if modo_sim:
            await avisar_tg(f"Oportunidade OKX->BYB: {lp1:.4f}% (${lu1:.4f})")
    elif lp2 >= lucro_min:
        st_op = "SIMULADO BYB->OKX" if modo_sim else await executar(q2, "BYB->OKX", lu2)
        if modo_sim:
            await avisar_tg(f"Oportunidade BYB->OKX: {lp2:.4f}% (${lu2:.4f})")

    return {
        "Hora": datetime.now().strftime("%H:%M:%S"),
        "OKX Ask": o_ask, "Bybit Bid": b_bid, "OKX->BYB %": round(lp1, 4), "OKX->BYB $": round(lu1, 4),
        "Bybit Ask": b_ask, "OKX Bid": o_bid, "BYB->OKX %": round(lp2, 4), "BYB->OKX $": round(lu2, 4),
        "Status": st_op
    }

if st.session_state.rodando:
    try:
        res = asyncio.run(rodar_ciclo())
        st.session_state.logs.insert(0, res)
        st.session_state.logs = st.session_state.logs[:50]
        with painel.container():
            st.success(f"Ativo — {res['Hora']}")
            m1, m2, m3, m4 = st.columns(4)
            m1.metric("OKX Ask", f"${res['OKX Ask']:,.2f}", f"Bid: ${res['OKX Bid']:,.2f}", delta_color="off")
            m2.metric("Bybit Ask", f"${res['Bybit Ask']:,.2f}", f"Bid: ${res['Bybit Bid']:,.2f}", delta_color="off")
            m3.metric("OKX → Bybit", f"{res['OKX->BYB %']:+.4f}%", f"${res['OKX->BYB $']:+.4f}")
            m4.metric("Bybit → OKX", f"{res['BYB->OKX %']:+.4f}%", f"${res['BYB->OKX $']:+.4f}")
            st.dataframe(pd.DataFrame(st.session_state.logs), use_container_width=True, hide_index=True)
    except Exception as e:
        painel.error(f"Aviso: {e}")
    asyncio.run(asyncio.sleep(intervalo))
    st.rerun()
else:
    painel.info("Toque em **▶ Iniciar Robô** para começar.")
# === FIM DO CODIGO ===
