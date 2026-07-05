"""
Positions page — current holdings with P&L.
"""

import numpy as np
import pandas as pd
import streamlit as st


def show_positions():
    from monitor.data_service import get_positions

    st.title("💼 持仓管理")

    positions_df = get_positions()
    has_data = not positions_df.empty and len(positions_df) > 0

    if not has_data:
        st.info("暂无持仓数据。运行回测或实盘交易后将在此显示。")
        return

    # Display positions from real data
    display_df = positions_df.rename(columns={
        "ts_code": "代码", "net_volume": "持仓",
        "avg_cost": "成本", "current_price": "现价",
        "market_value": "市值", "pnl_pct": "盈亏%",
    }).copy()

    display_df["成本"] = display_df["成本"].apply(lambda x: f"¥{x:.2f}")
    display_df["现价"] = display_df["现价"].apply(lambda x: f"¥{x:.2f}")
    display_df["市值"] = display_df["市值"].apply(lambda x: f"¥{x:,.0f}")
    display_df["盈亏%"] = display_df["盈亏%"].apply(lambda x: f"{x*100:+.2f}%")

    cols = ["代码", "持仓", "成本", "现价", "市值", "盈亏%"]
    st.dataframe(display_df[cols], hide_index=True, use_container_width=True)

    # Summary
    st.divider()
    c1, c2, c3, c4 = st.columns(4)
    total_mv = positions_df["market_value"].sum()
    total_pnl = (positions_df["current_price"] * positions_df["net_volume"]).sum() - (positions_df["avg_cost"] * positions_df["net_volume"]).sum()
    # Estimate cash from total MV (assume 80% invested)
    total_capital = total_mv / 0.80 if total_mv > 0 else 200_000
    cash = total_capital - total_mv

    c1.metric("总市值", f"¥{total_mv:,.0f}")
    c2.metric("可用资金", f"¥{cash:,.0f}")
    c3.metric("持仓盈亏", f"¥{total_pnl:+,.0f}")
    c4.metric("仓位比例", f"{total_mv/total_capital*100:.1f}%" if total_mv > 0 else "0%")

    # Allocation chart
    if total_mv > 0:
        st.divider()
        st.subheader("仓位分布")
        import plotly.express as px
        alloc_data = positions_df.copy()
        alloc_data["weight"] = alloc_data["market_value"] / total_mv * 100
        fig = px.pie(alloc_data, values="weight", names="ts_code", hole=0.4)
        st.plotly_chart(fig, use_container_width=True)
