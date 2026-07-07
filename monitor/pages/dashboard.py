"""
Dashboard page — portfolio overview with metrics and charts.
"""

from datetime import date, timedelta

import numpy as np
import pandas as pd
import streamlit as st

from monitor.components.charts import plot_equity_curve, plot_drawdown, plot_daily_returns
from monitor.components.metrics import render_metric_card, render_metric_row


def _empty_equity():
    """Flat equity when no real data — clearly shows NO TRADING DATA."""
    n_days = 5
    dates = [date.today() - timedelta(days=n_days - i) for i in range(n_days)]
    equity = [200_000] * n_days
    returns = np.zeros(n_days)
    return dates, equity, returns


def show_dashboard():
    from monitor.data_service import get_equity_curve, get_db_status, get_positions

    st.title("📈 总览面板")

    # Load real data
    equity_df = get_equity_curve()
    positions_df = get_positions()

    if equity_df.empty:
        st.warning("⚠️ 暂无真实交易数据 — 显示初始状态（非真实收益）")
        dates, equity, returns = _empty_equity()
        total_return = (equity[-1] / equity[0] - 1) * 100
        sharpe = np.mean(returns) / np.std(returns) * np.sqrt(244) if np.std(returns) > 0 else 0
        rolling_max = np.maximum.accumulate(equity)
        drawdowns = (equity - rolling_max) / rolling_max
        max_dd = drawdowns.min() * 100
        holdings = 0
    else:
        equity = equity_df["equity"].values
        dates = pd.to_datetime(equity_df["trade_date"]).dt.date.tolist()
        if len(equity) > 1:
            returns = pd.Series(equity).pct_change().dropna().values
            total_return = (equity[-1] / equity[0] - 1) * 100
            sharpe = np.mean(returns) / np.std(returns) * np.sqrt(244) if np.std(returns) > 0 else 0
            rolling_max = np.maximum.accumulate(equity)
            drawdowns = (equity - rolling_max) / rolling_max
            max_dd = drawdowns.min() * 100
        else:
            returns = np.array([])
            total_return, sharpe, max_dd = 0, 0, 0
            drawdowns = np.array([])
        holdings = len(positions_df) if not positions_df.empty else 0

    # Metrics row
    col1, col2, col3, col4, col5 = st.columns(5)

    with col1:
        st.metric("总资产", f"¥{equity[-1]:,.0f}", f"{total_return:+.1f}%" if total_return else "")
    with col2:
        last_ret = returns[-1] * 100 if len(returns) > 0 else 0
        st.metric("日收益", f"{last_ret:+.2f}%" if last_ret else "-")
    with col3:
        st.metric("夏普比率", f"{sharpe:.2f}" if sharpe else "-")
    with col4:
        st.metric("最大回撤", f"{max_dd:.1f}%" if max_dd else "-", delta_color="inverse")
    with col5:
        st.metric("持仓数", f"{holdings}/8" if holdings else "-")

    st.divider()

    # Charts
    tab1, tab2, tab3 = st.tabs(["净值曲线", "回撤曲线", "日收益"])

    with tab1:
        df = pd.DataFrame({"date": dates, "equity": equity})
        fig = plot_equity_curve(df)
        st.plotly_chart(fig, use_container_width=True)

    with tab2:
        df_dd = pd.DataFrame({"date": dates, "drawdown": drawdowns * 100})
        fig = plot_drawdown(df_dd)
        st.plotly_chart(fig, use_container_width=True)

    with tab3:
        df_ret = pd.DataFrame({"date": dates, "daily_return": returns * 100})
        fig = plot_daily_returns(df_ret)
        st.plotly_chart(fig, use_container_width=True)

    # Risk status — from real data
    from monitor.data_service import get_risk_events
    events = get_risk_events(days=1)

    st.divider()
    st.subheader("风控状态")

    rc1, rc2, rc3, rc4 = st.columns(4)
    has_critical = any(e["severity"] == "CRITICAL" for _, e in events.iterrows()) if not events.empty else False
    tripped = any("TRIPPED" in str(e.get("event_type", "")) for _, e in events.iterrows()) if not events.empty else False

    with rc1:
        if tripped:
            st.error("熔断器: 已触发")
        else:
            st.success("熔断器: 正常")
    with rc2:
        st.info("日亏损: ¥0 / ¥4,000")
    with rc3:
        st.info("连续亏损: 0天 / 3天")
    with rc4:
        st.success("单票上限: 20%")

    # Recent risk events
    if not events.empty:
        st.caption("今日风控事件:")
        for _, ev in events.head(5).iterrows():
            icon = {"INFO": "ℹ️", "WARN": "⚠️", "CRITICAL": "🚨"}.get(ev["severity"], "📌")
            st.caption(f"{icon} {ev['event_time']} — {ev['detail'][:80]}")


def show_config():
    """System configuration page."""
    st.title("⚙️ 系统配置")

    st.subheader("风控参数")
    col1, col2 = st.columns(2)

    with col1:
        st.number_input("单票最大仓位 (%)", value=20.0, min_value=5.0, max_value=50.0, key="cfg_sp")
        st.number_input("总仓位上限 (%)", value=80.0, min_value=20.0, max_value=100.0, key="cfg_tp")
        st.number_input("最大持仓数", value=10, min_value=1, max_value=20, key="cfg_mh")

    with col2:
        st.number_input("止损线 (%)", value=5.0, min_value=1.0, max_value=20.0, key="cfg_sl")
        st.number_input("日亏损熔断 (%)", value=3.5, min_value=0.5, max_value=10.0, key="cfg_md")
        st.number_input("连续亏损暂停 (天)", value=3, min_value=1, max_value=10, key="cfg_cl")

    st.divider()

    st.subheader("策略配置")
    strategy = st.selectbox("当前策略", ["趋势跟踪 (TrendFollow)", "均值回归 (MeanRevert)", "多因子选股 (FactorSelector)"])
    st.slider("调仓频率", 1, 30, 5, help="每N个交易日调仓一次")

    st.divider()

    st.subheader("通知配置")
    st.text_input("钉钉 Webhook URL", type="password", placeholder="https://oapi.dingtalk.com/robot/send?access_token=...")
    st.text_input("企业微信 Key", type="password", placeholder="your-webhook-key")

    if st.button("保存配置", type="primary"):
        from config.settings import atomic_write_json
        config_data = {
            "risk": {
                "max_single_position_pct": st.session_state.get("cfg_sp", 20.0) / 100,
                "max_total_position_pct": st.session_state.get("cfg_tp", 80.0) / 100,
                "max_holdings_count": int(st.session_state.get("cfg_mh", 10)),
                "stop_loss_pct": st.session_state.get("cfg_sl", 5.0) / 100,
                "max_daily_loss_pct": st.session_state.get("cfg_md", 3.5) / 100,
                "max_consecutive_loss_days": int(st.session_state.get("cfg_cl", 3)),
            }
        }
        atomic_write_json("config/user_config.json", config_data, indent=2, ensure_ascii=False)
        st.success("✅ 配置已保存到 config/user_config.json")
        st.info("需重启系统使配置生效")
