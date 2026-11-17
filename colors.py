"""Color palette used for code highlighting."""

COLOR_PALETTE = [
    ("#FFD966", "Yellow"),
    ("#92D050", "Light Green"),
    ("#00B050", "Green"),
    ("#00B0F0", "Blue"),
    ("#0070C0", "Dark Blue"),
    ("#7030A0", "Purple"),
    ("#FF6D9B", "Pink"),
    ("#FF4444", "Red"),
    ("#FFC000", "Orange"),
    ("#70AD47", "Sage Green"),
    ("#4472C4", "Royal Blue"),
    ("#A349A4", "Plum"),
    ("#ED7D31", "Burnt Orange"),
    ("#5B9BD5", "Sky Blue"),
    ("#FFC7CE", "Light Pink"),
    ("#C6E0B4", "Pale Green"),
]

DEFAULT_CODE_COLOR = "#FFD966"


def color_for_index(index: int) -> str:
    """Cycle through the palette for auto-assigning colors (e.g. on import)."""
    return COLOR_PALETTE[index % len(COLOR_PALETTE)][0]
