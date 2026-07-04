"""
Signals page — trading signals log and strategy output.
"""

from datetime import date, datetime

import pandas as pd
import streamlit as st


def show_signals():
    from monitor.data_service import get_recent_signals, get_risk_events

    st.title("📡 信号日志")

    signals_df = get_recent_signals()

    if signals_df.empty:
        st.info("暂无信号数据")
    else:
        filter_type = st.selectbox("信号类型", ["全部", "买入", "卖出"])
        type_map = {"买入": "BUY", "卖出": "SELL"}
        display = signals_df if filter_type == "全部" else signals_df[signals_df["signal_type"] == type_map.get(filter_type, "")]
        st.dataframe(display, hide_index=True, use_container_width=True)

        st.divider()
        st.subheader("信号统计 (30天)")
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("总信号", len(signals_df))
        c2.metric("买入", len(signals_df[signals_df["signal_type"] == "BUY"]))
        c3.metric("卖出", len(signals_df[signals_df["signal_type"] == "SELL"]))
        c4.metric("已执行", len(signals_df[signals_df["executed"] == True]))

    # Risk events
    st.divider()
    st.subheader("风控事件")
    events_df = get_risk_events()

    if events_df.empty:
        st.info("暂无风控事件")
    else:
        for _, ev in events_df.head(20).iterrows():
            icon = {"INFO": "ℹ️", "WARN": "⚠️", "CRITICAL": "🚨"}.get(ev["severity"], "📌")
            color = {"INFO": "blue", "WARN": "orange", "CRITICAL": "red"}.get(ev["severity"], "grey")
            st.markdown(f":{color}[{icon} {ev['event_time']}] — {ev['detail']}")
