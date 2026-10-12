"""Plotly charts. Kept separate so the app (Stage 4) and the command line use
the same figures."""

from __future__ import annotations

import plotly.graph_objects as go

# Colors (diverging pair + neutral midpoint, chart ink)
BELOW = "#e34948"      # value below current price
NEUTRAL = "#f0efec"    # value close to current price
ABOVE = "#2a78d6"      # value above current price
INK = "#0b0b0b"
INK_SECONDARY = "#52514e"
MUTED = "#898781"
GRID = "#e6e5e0"
SURFACE = "#fcfcfb"

# Football field: one categorical hue per group, in the palette's fixed order
# (slots 1-3 of the palette; dict order = legend order, same as the rows top-down)
GROUP_COLORS = {"Market": "#1baf7a", "Trading comps": "#2a78d6", "DCF": "#eb6834"}
FONT = {"family": "Inter, Helvetica, Arial, sans-serif", "color": INK}


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
        # type=category: otherwise Plotly reads labels such as "7.5%" as numbers and misplaces the cells
        xaxis={"title": table.columns.name, "side": "bottom", "tickfont": {"color": MUTED}, "type": "category"},
        yaxis={"title": "WACC", "autorange": "reversed", "tickfont": {"color": MUTED}, "type": "category"},
        plot_bgcolor=SURFACE, paper_bgcolor=SURFACE,
        font={"family": "Inter, Helvetica, Arial, sans-serif", "color": INK},
        margin={"l": 70, "r": 20, "t": 80, "b": 60}, height=480,
    )
    return fig


def football_field(rows, current_price: float, title: str) -> go.Figure:
    """Horizontal floating bars, one per valuation method (low to high value
    per share), a thin tick at the mid point (peer median / DCF base case)
    and a dashed line at the current share price.

    `rows` is summary.football_field_data(): label, group, low, mid, high, basis.
    Rows with a missing low or high are left out (shown as N/A in the tables)."""
    rows = rows.dropna(subset=["low", "high"]).iloc[::-1]   # Plotly draws bottom-up: first row at the top
    fig = go.Figure()
    for group, color in GROUP_COLORS.items():
        part = rows[rows["group"] == group]
        if part.empty:
            continue
        fig.add_trace(go.Bar(
            name=group, orientation="h", y=part["label"], base=part["low"],
            x=part["high"] - part["low"], marker={"color": color, "line": {"width": 0}},
            width=0.55, customdata=part[["low", "mid", "high", "basis"]].values,
            hovertemplate=("<b>%{y}</b><br>Low $%{customdata[0]:,.2f}"
                           "<br>Mid $%{customdata[1]:,.2f}<br>High $%{customdata[2]:,.2f}"
                           "<br>%{customdata[3]}<extra></extra>"),
        ))
    mids = rows.dropna(subset=["mid"])
    fig.add_trace(go.Scatter(
        name="Median / base case", x=mids["mid"], y=mids["label"], mode="markers",
        marker={"symbol": "line-ns", "size": 22, "line": {"width": 3, "color": INK}},
        hovertemplate="<b>%{y}</b><br>Mid $%{x:,.2f}<extra></extra>",
    ))
    # Direct labels at both ends of each bar, in text ink (not the series color)
    for _, r in rows.iterrows():
        for x, anchor, shift in ((r["low"], "right", -6), (r["high"], "left", 6)):
            fig.add_annotation(x=x, y=r["label"], text=f"${x:,.0f}", showarrow=False,
                               xanchor=anchor, xshift=shift,
                               font={"size": 12, "color": INK_SECONDARY})
    fig.add_vline(x=current_price, line={"color": INK, "width": 2, "dash": "dash"})
    fig.add_annotation(x=current_price, y=1.0, yref="paper", yanchor="bottom",
                       text=f"Current price ${current_price:,.2f}", showarrow=False,
                       font={"size": 12, "color": INK})

    lo, hi = rows["low"].min(), rows["high"].max()
    pad = (hi - lo) * 0.12
    fig.update_layout(
        title={"text": title, "x": 0},
        barmode="overlay", bargap=0.35,
        xaxis={"title": "Value per share (USD)", "tickprefix": "$", "range": [max(lo - pad, 0), hi + pad],
               "gridcolor": GRID, "zeroline": False, "tickfont": {"color": MUTED}},
        yaxis={"tickfont": {"color": INK}, "automargin": True,
               "categoryorder": "array", "categoryarray": list(rows["label"])},
        legend={"orientation": "h", "yanchor": "top", "y": -0.18, "x": 0},
        plot_bgcolor=SURFACE, paper_bgcolor=SURFACE, font=FONT,
        margin={"l": 20, "r": 30, "t": 80, "b": 80}, height=120 + 52 * len(rows),
        hoverlabel={"bgcolor": "white", "font": {"color": INK}},
    )
    return fig
