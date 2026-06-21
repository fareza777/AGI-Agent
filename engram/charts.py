"""Chart rendering for generated documents.

Turns a create_document `table` + a small `chart` spec into a PNG using
matplotlib (Agg backend, no display needed). Embedding the PNG into docx/pptx
is what lifts a report from "a wall of numbers" to something a reader actually
absorbs.

Everything degrades gracefully: if matplotlib isn't installed, or the spec
references columns that don't exist, render() returns None and the caller just
omits the chart — a document must never fail because a chart couldn't be drawn.
"""

import logging

log = logging.getLogger("engram.charts")

# Palette matched to the document theme (desktop._PRIMARY / _ACCENT family).
_COLORS = ["#1F3864", "#2E74B5", "#5B9BD5", "#9DC3E6", "#ED7D31", "#70AD47",
           "#FFC000", "#A5A5A5"]

_KINDS = ("bar", "barh", "line", "pie")


def _to_number(value):
    """Parse a cell into a float, tolerating thousands separators / currency."""
    text = str(value).strip()
    cleaned = "".join(c for c in text if c.isdigit() or c in ".-")
    try:
        return float(cleaned)
    except ValueError:
        return None


def render(table: dict, spec: dict, out_path: str) -> str:
    """Render a chart PNG to out_path. Returns out_path on success, else None.

    spec: {
      "type": "bar" | "barh" | "line" | "pie",   (default "bar")
      "label_col": int = 0,   # column used for category labels
      "value_col": int = 1,   # column used for numeric values
      "title": str            # optional chart title
    }
    """
    if not table or not table.get("headers") or not table.get("rows"):
        return None
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception:
        log.debug("matplotlib unavailable — skipping chart")
        return None

    spec = spec or {}
    kind = str(spec.get("type", "bar")).lower()
    if kind not in _KINDS:
        kind = "bar"
    headers = table["headers"]
    rows = table["rows"]
    lcol = int(spec.get("label_col", 0))
    vcol = int(spec.get("value_col", 1 if len(headers) > 1 else 0))

    labels, values = [], []
    for row in rows:
        if vcol >= len(row):
            continue
        num = _to_number(row[vcol])
        if num is None:
            continue
        labels.append(str(row[lcol]) if lcol < len(row) else "")
        values.append(num)
    if not values:
        return None  # nothing numeric to plot

    title = spec.get("title") or (headers[vcol] if vcol < len(headers) else "")

    try:
        fig, ax = plt.subplots(figsize=(7.2, 4.2), dpi=150)
        if kind == "pie":
            ax.pie(values, labels=labels, autopct="%1.0f%%",
                   colors=_COLORS, textprops={"fontsize": 9})
            ax.axis("equal")
        elif kind == "line":
            ax.plot(labels, values, marker="o", color=_COLORS[0], linewidth=2)
            ax.grid(True, axis="y", alpha=0.3)
        elif kind == "barh":
            ax.barh(labels, values, color=_COLORS[:len(values)] or _COLORS[0])
            ax.invert_yaxis()
        else:  # bar
            ax.bar(labels, values, color=_COLORS[:len(values)] or _COLORS[0])
            ax.grid(True, axis="y", alpha=0.3)
        if kind != "pie":
            for label in ax.get_xticklabels():
                label.set_rotation(30)
                label.set_horizontalalignment("right")
        if title:
            ax.set_title(title, fontsize=12, fontweight="bold", color="#1F3864")
        fig.tight_layout()
        fig.savefig(out_path, bbox_inches="tight")
        plt.close(fig)
        return out_path
    except Exception:
        log.debug("chart render failed", exc_info=True)
        try:
            plt.close("all")
        except Exception:
            pass
        return None
