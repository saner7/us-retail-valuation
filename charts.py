"""Plotly charts. Kept separate so the app (Stage 4) and the command line use
the same figures."""

from __future__ import annotations

import plotly.graph_objects as go

import config

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
# (palette slots; dict order = legend order, same as the rows top-down)
# Neighbouring rows must stay distinguishable: in row order Market, Comps,
# Precedents, DCF this assignment passes the palette validator (CVD and normal
# vision); yellow next to orange did not. Every bar carries direct value labels
# because yellow and aqua are below 3:1 contrast on the surface.
GROUP_COLORS = {"Market": "#eda100", "Trading comps": "#2a78d6", "Precedent transactions": "#1baf7a",
                "DCF": "#eb6834"}
FONT = {"family": "Inter, Helvetica, Arial, sans-serif", "color": INK}


def sensitivity_heatmap(table, current_price: float, title: str, x_is_multiple: bool = False,
                        base_x=None, base_wacc=None, currency: str = "USD") -> go.Figure:
    """Value per share for every WACC (rows) x terminal growth / exit multiple
    (columns) combination.

    Color is diverging around the CURRENT SHARE PRICE: red = model value below
    the market price, blue = above, gray = about the same. So you can see at a
    glance which assumptions would justify today's price.
    """
    x_labels = [f"{x:.1f}x" if x_is_multiple else f"{x:.1%}" for x in table.columns]
    y_labels = [f"{w:.1%}" for w in table.index]
    z = table.values.astype(float)
    sym = config.currency_symbol(currency)

    fig = go.Figure(go.Heatmap(
        z=z, x=x_labels, y=y_labels,
        colorscale=[[0, BELOW], [0.5, NEUTRAL], [1, ABOVE]],
        zmid=current_price,
        texttemplate=f"{sym}%{{z:,.0f}}", textfont={"color": INK, "size": 12},
        xgap=2, ygap=2,   # thin surface-colored gap between cells
        colorbar={"title": {"text": "Value / share", "side": "right"}, "tickprefix": sym},
        hovertemplate=(f"WACC %{{y}}<br>{table.columns.name} %{{x}}"
                       f"<br>Value per share {sym}%{{z:,.2f}}<extra></extra>"),
    ))

    # Outline the base case so it is easy to find
    if base_x is not None and base_wacc is not None:
        cols, rows = list(table.columns), list(table.index)
        i = min(range(len(cols)), key=lambda k: abs(cols[k] - base_x))
        j = min(range(len(rows)), key=lambda k: abs(rows[k] - base_wacc))
        fig.add_shape(type="rect", x0=i - 0.5, x1=i + 0.5, y0=j - 0.5, y1=j + 0.5,
                      line={"color": INK, "width": 2})

    fig.update_layout(
        title={"text": f"{title}<br><sup>Current price {sym}{current_price:,.2f} "
                       f"(gray). Base case outlined.</sup>", "x": 0},
        # type=category: otherwise Plotly reads labels such as "7.5%" as numbers and misplaces the cells
        xaxis={"title": table.columns.name, "side": "bottom", "tickfont": {"color": MUTED}, "type": "category"},
        yaxis={"title": "WACC", "autorange": "reversed", "tickfont": {"color": MUTED}, "type": "category"},
        plot_bgcolor=SURFACE, paper_bgcolor=SURFACE,
        font={"family": "Inter, Helvetica, Arial, sans-serif", "color": INK},
        margin={"l": 70, "r": 20, "t": 80, "b": 60}, height=480,
    )
    return fig


def football_field(rows, current_price: float, title: str, currency: str = "USD") -> go.Figure:
    """Horizontal floating bars, one per valuation method (low to high value
    per share), a thin tick at the mid point (peer median / DCF base case)
    and a dashed line at the current share price.

    `rows` is summary.football_field_data(): label, group, low, mid, high, basis.
    Rows with a missing low or high are left out (shown as N/A in the tables)."""
    rows = rows.dropna(subset=["low", "high"]).iloc[::-1]   # Plotly draws bottom-up: first row at the top
    sym = config.currency_symbol(currency)
    fig = go.Figure()
    for group, color in GROUP_COLORS.items():
        part = rows[rows["group"] == group]
        if part.empty:
            continue
        fig.add_trace(go.Bar(
            name=group, orientation="h", y=part["label"], base=part["low"],
            x=part["high"] - part["low"], marker={"color": color, "line": {"width": 0}},
            width=0.55, customdata=part[["low", "mid", "high", "basis"]].values,
            hovertemplate=(f"<b>%{{y}}</b><br>Low {sym}%{{customdata[0]:,.2f}}"
                           f"<br>Mid {sym}%{{customdata[1]:,.2f}}<br>High {sym}%{{customdata[2]:,.2f}}"
                           "<br>%{customdata[3]}<extra></extra>"),
        ))
    mids = rows.dropna(subset=["mid"])
    fig.add_trace(go.Scatter(
        name="Median / base case", x=mids["mid"], y=mids["label"], mode="markers",
        marker={"symbol": "line-ns", "size": 22, "line": {"width": 3, "color": INK}},
        hovertemplate=f"<b>%{{y}}</b><br>Mid {sym}%{{x:,.2f}}<extra></extra>",
    ))
    # Bear / Bull scenario values on the DCF bars (the bar range itself is unchanged)
    for col, name, symbol in (("bear", "Bear scenario", "triangle-left"), ("bull", "Bull scenario", "triangle-right")):
        if col not in rows:
            continue
        pts = rows.dropna(subset=[col])
        if pts.empty:
            continue
        fig.add_trace(go.Scatter(
            name=name, x=pts[col], y=pts["label"], mode="markers",
            marker={"symbol": symbol, "size": 13, "color": INK, "line": {"width": 2, "color": SURFACE}},
            hovertemplate=f"<b>%{{y}}</b><br>{name} {sym}%{{x:,.2f}}<extra></extra>",
        ))
    # Direct labels at both ends of each bar, in text ink (not the series color)
    for _, r in rows.iterrows():
        for x, anchor, shift in ((r["low"], "right", -6), (r["high"], "left", 6)):
            fig.add_annotation(x=x, y=r["label"], text=f"{sym}{x:,.0f}", showarrow=False,
                               xanchor=anchor, xshift=shift,
                               font={"size": 12, "color": INK_SECONDARY})
    fig.add_vline(x=current_price, line={"color": INK, "width": 2, "dash": "dash"})
    fig.add_annotation(x=current_price, y=1.0, yref="paper", yanchor="bottom",
                       text=f"Current price {sym}{current_price:,.2f}", showarrow=False,
                       font={"size": 12, "color": INK})

    plot_height = 40 + 52 * len(rows)
    extra = [rows[c] for c in ("bear", "bull") if c in rows]
    lo = min([rows["low"].min()] + [x.min() for x in extra if x.notna().any()])
    hi = max([rows["high"].max()] + [x.max() for x in extra if x.notna().any()])
    pad = (hi - lo) * 0.12
    fig.update_layout(
        title={"text": title, "x": 0},
        barmode="overlay", bargap=0.35,
        xaxis={"title": f"Value per share ({currency})", "tickprefix": sym, "range": [max(lo - pad, 0), hi + pad],
               "gridcolor": GRID, "zeroline": False, "tickfont": {"color": MUTED}},
        yaxis={"tickfont": {"color": INK}, "automargin": True,
               "categoryorder": "array", "categoryarray": list(rows["label"])},
        # Legend a fixed distance below the plot, whatever the number of rows
        legend={"orientation": "h", "yanchor": "top", "y": -70 / plot_height, "x": 0},
        plot_bgcolor=SURFACE, paper_bgcolor=SURFACE, font=FONT,
        margin={"l": 20, "r": 30, "t": 80, "b": 110}, height=190 + plot_height,
        hoverlabel={"bgcolor": "white", "font": {"color": INK}},
    )
    return fig


# Scenario chart: one categorical hue per valuation method (palette slots 1-2)
METHOD_COLORS = {"Gordon growth": "#2a78d6", "Exit multiple": "#eb6834"}


def scenario_chart(table, current_price: float, title: str, currency: str = "USD") -> go.Figure:
    """Value per share by scenario (rows) for both DCF methods (dots), plus the
    probability-weighted value, against the current price (dashed line).
    `table` is scenarios.results_table()."""
    sym = config.currency_symbol(currency)
    rows = [("bear", "Bear"), ("base", "Base"), ("bull", "Bull"), ("weighted", "Probability-weighted")]
    labels = [label for _, label in rows][::-1]          # Plotly draws bottom-up
    fig = go.Figure()
    for (method, color), key in zip(METHOD_COLORS.items(), ["gordon_value", "exit_value"]):
        xs = [table.loc[key, col] for col, _ in rows]
        fig.add_trace(go.Scatter(
            name=method, x=xs, y=[label for _, label in rows], mode="markers+text",
            marker={"size": 13, "color": color, "line": {"width": 2, "color": SURFACE}},
            text=[("" if x != x else f"{sym}{x:,.0f}") for x in xs], textposition="top center",
            textfont={"color": INK_SECONDARY, "size": 12},
            hovertemplate=f"<b>%{{y}}</b><br>{method} {sym}%{{x:,.2f}}<extra></extra>",
        ))
    fig.add_vline(x=current_price, line={"color": INK, "width": 2, "dash": "dash"})
    fig.add_annotation(x=current_price, y=1.0, yref="paper", yanchor="bottom",
                       text=f"Current price {sym}{current_price:,.2f}", showarrow=False,
                       font={"size": 12, "color": INK})
    fig.update_layout(
        title={"text": title, "x": 0},
        xaxis={"title": f"Value per share ({currency})", "tickprefix": sym, "gridcolor": GRID,
               "zeroline": False, "tickfont": {"color": MUTED}},
        # Extra room above the top row so its value labels are not clipped
        yaxis={"categoryorder": "array", "categoryarray": labels, "tickfont": {"color": INK}, "automargin": True,
               "range": [-0.5, len(labels) - 0.2]},
        legend={"orientation": "h", "yanchor": "top", "y": -0.25, "x": 0},
        plot_bgcolor=SURFACE, paper_bgcolor=SURFACE, font=FONT,
        margin={"l": 20, "r": 30, "t": 80, "b": 90}, height=400,
        hoverlabel={"bgcolor": "white", "font": {"color": INK}},
    )
    return fig
