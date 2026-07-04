"""
Chart components for Streamlit monitor.

Uses Plotly for interactive charts.
"""

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots


def plot_equity_curve(df: pd.DataFrame) -> go.Figure:
    """Plot equity curve with benchmark overlay area.

    Args:
        df: DataFrame with columns [date, equity].

    Returns:
        Plotly figure.
    """
    fig = go.Figure()

    fig.add_trace(go.Scatter(
        x=df["date"],
        y=df["equity"],
        mode="lines",
        name="策略净值",
        line=dict(color="#2563eb", width=2),
        fill="tozeroy",
        fillcolor="rgba(37, 99, 235, 0.1)",
    ))

    # Add starting capital reference line
    initial = df["equity"].iloc[0]
    fig.add_hline(
        y=initial,
        line_dash="dash",
        line_color="gray",
        opacity=0.5,
        annotation_text=f"初始资金 ¥{initial:,.0f}",
    )

    fig.update_layout(
        height=400,
        margin=dict(l=0, r=0, t=0, b=0),
        hovermode="x unified",
        yaxis=dict(
            tickprefix="¥",
            tickformat=",.0f",
            gridcolor="rgba(0,0,0,0.05)",
        ),
        xaxis=dict(gridcolor="rgba(0,0,0,0.05)"),
        plot_bgcolor="white",
    )

    return fig


def plot_drawdown(df: pd.DataFrame) -> go.Figure:
    """Plot drawdown as filled area chart.

    Args:
        df: DataFrame with columns [date, drawdown] where drawdown is in percent.
    """
    fig = go.Figure()

    fig.add_trace(go.Scatter(
        x=df["date"],
        y=df["drawdown"],
        mode="lines",
        name="回撤",
        line=dict(color="#dc2626", width=1),
        fill="tozeroy",
        fillcolor="rgba(220, 38, 38, 0.15)",
    ))

    fig.update_layout(
        height=300,
        margin=dict(l=0, r=0, t=0, b=0),
        hovermode="x unified",
        yaxis=dict(
            ticksuffix="%",
            gridcolor="rgba(0,0,0,0.05)",
            autorange="reversed",  # Drawdowns go negative → upward is better
        ),
        xaxis=dict(gridcolor="rgba(0,0,0,0.05)"),
        plot_bgcolor="white",
    )

    return fig


def plot_daily_returns(df: pd.DataFrame) -> go.Figure:
    """Plot daily returns as bar chart with color coding.

    Args:
        df: DataFrame with columns [date, daily_return] where return is in percent.
    """
    colors = ["#16a34a" if v >= 0 else "#dc2626" for v in df["daily_return"]]

    fig = go.Figure()

    fig.add_trace(go.Bar(
        x=df["date"],
        y=df["daily_return"],
        marker_color=colors,
        name="日收益",
    ))

    fig.update_layout(
        height=300,
        margin=dict(l=0, r=0, t=0, b=0),
        hovermode="x unified",
        yaxis=dict(
            ticksuffix="%",
            gridcolor="rgba(0,0,0,0.05)",
        ),
        xaxis=dict(gridcolor="rgba(0,0,0,0.05)"),
        plot_bgcolor="white",
        showlegend=False,
    )

    return fig
