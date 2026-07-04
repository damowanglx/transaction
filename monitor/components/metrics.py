"""
Metric card components for Streamlit monitor.

Reusable metric display helpers.
"""

import streamlit as st


def render_metric_card(
    label: str,
    value: str,
    delta: str = "",
    delta_color: str = "normal",
    help_text: str = "",
):
    """Render a single metric card using Streamlit's native metric."""
    st.metric(
        label=label,
        value=value,
        delta=delta,
        delta_color=delta_color,
        help=help_text,
    )


def render_metric_row(metrics: list[dict]):
    """Render a row of metric cards in columns.

    Args:
        metrics: List of dicts with keys: label, value, delta, delta_color, help.
    """
    cols = st.columns(len(metrics))
    for col, m in zip(cols, metrics):
        with col:
            st.metric(
                label=m.get("label", ""),
                value=m.get("value", ""),
                delta=m.get("delta"),
                delta_color=m.get("delta_color", "normal"),
                help=m.get("help"),
            )


def render_risk_status(status: str):
    """Render a risk status indicator."""
    colors = {
        "NORMAL": ("✅", "正常"),
        "WARNING": ("⚠️", "警告"),
        "TRIPPED": ("🚨", "熔断"),
        "COOLING": ("🟡", "冷却中"),
    }
    emoji, label = colors.get(status, ("❓", "未知"))
    st.markdown(f"{emoji} **风控状态: {label}**")
