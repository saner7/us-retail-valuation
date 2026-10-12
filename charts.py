"""Plotly charts. Kept separate so the app (Stage 4) and the command line use
the same figures."""

from __future__ import annotations

import plotly.graph_objects as go

# Colors (diverging pair + neutral midpoint, chart ink)
BELOW = "#e34948"      # value below current price
NEUTRAL = "#f0efec"    # value close to current price
ABOVE = "#2a78d6"      # value above current price
INK = "#0b0b0b"
MUTED = "#898781"
SURFACE = "#fcfcfb"


def sensitivity_heatmap(table, current_price: float, title: str, x_is_multiple: bool = False,
                        base_x=None, base_wacc=None) -> go.Figure:
    """Value per share for every WACC (rows) x terminal growth / exit multiple
    (columns) combination.

    Color is diverging around the CURRENT SHARE PRICE: red = model value below
    the market price, blue = above, gray = about the same. So you can see at a
    glance which assumptions would justify today's price.
    """
    x_labels = [f"{x:.1f}x" if x_is_multiple else f"{x:.1%}" for x in table.columns]
    y_labels = [f"{w:.1%}" for w in table.index]
    z = table.values.astype(float)

    fig = go.Figure(go.Heatmap(
        z=z, x=x_labels, y=y_labels,
        colorscale=[[0, BELOW], [0.5, NEUTRAL], [1, ABOVE]],
        zmid=current_price,
        texttemplate="$%{z:,.0f}", textfont={"color": INK, "size": 12},
        xgap=2, ygap=2,   # thin surface-colored gap between cells
        colorbar={"title": {"text": "Value / share", "side": "right"}, "tickprefix": "$"},
        hovertemplate=(f"WACC %{{y}}<br>{table.columns.name} %{{x}}"
                       "<br>Value per share $%{z:,.2f}<extra></extra>"),
    ))

    # Outline the base case so it is easy to find
    if base_x is not None and base_wacc is not None:
        cols, rows = list(table.columns), list(table.index)
        i = min(range(len(cols)), key=lambda k: abs(cols[k] - base_x))
        j = min(range(len(rows)), key=lambda k: abs(rows[k] - base_wacc))
        fig.add_shape(type="rect", x0=i - 0.5, x1=i + 0.5, y0=j - 0.5, y1=j + 0.5,
                      line={"color": INK, "width": 2})

    fig.update_layout(
        title={"text": f"{title}<br><sup>Current price ${current_price:,.2f} "
                       f"(gray). Base case outlined.</sup>", "x": 0},
        xaxis={"title": table.columns.name, "side": "bottom", "tickfont": {"color": MUTED}},
        yaxis={"title": "WACC", "autorange": "reversed", "tickfont": {"color": MUTED}},
        plot_bgcolor=SURFACE, paper_bgcolor=SURFACE,
        font={"family": "Inter, Helvetica, Arial, sans-serif", "color": INK},
        margin={"l": 70, "r": 20, "t": 80, "b": 60}, height=480,
    )
    return fig
