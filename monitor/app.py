"""
Streamlit监控面板 — A股量化交易系统

Run: streamlit run monitor/app.py

Pages:
- Dashboard: Portfolio overview, P&L curve, risk status
- Positions: Current holdings, entry prices, P&L
- Signals: Recent signals, strategy output
"""

import sys
from pathlib import Path

# Ensure project root on path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import streamlit as st

st.set_page_config(
    page_title="A股量化交易系统",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded",
)

# Sidebar navigation
from monitor.data_service import get_db_status

db = get_db_status()

st.sidebar.title("📊 量化交易系统")

page = st.sidebar.radio(
    "导航",
    ["📈 总览面板", "💼 持仓管理", "📡 信号日志", "⚙️ 系统配置"],
    label_visibility="collapsed",
)

st.sidebar.divider()
st.sidebar.caption("资金: ¥200,000")

# Detect actual system state from DB connectivity
is_live = db["clickhouse"] and db["postgres"]
st.sidebar.caption(f"状态: {'🟢 在线' if is_live else '🔴 离线'}")
st.sidebar.caption(f"ClickHouse: {'🟢' if db['clickhouse'] else '🔴'}")
st.sidebar.caption(f"PostgreSQL: {'🟢' if db['postgres'] else '🔴'}")

# Page routing
if "📈" in page:
    from monitor.pages.dashboard import show_dashboard
    show_dashboard()
elif "💼" in page:
    from monitor.pages.positions import show_positions
    show_positions()
elif "📡" in page:
    from monitor.pages.signals import show_signals
    show_signals()
else:
    from monitor.pages.dashboard import show_config
    show_config()
