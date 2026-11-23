"""The page frame: a page declares its parts once; the frame places them for either interface.

    page_frame(page, layout,
               action=(record_button, status),          # the page's main action + its readout
               left=(select_channels, mode_segments),   # row 1, left: picker, toggle, segments
               middle=name_label,                       # row 1, the stretch between (optional)
               tools=(...), recent=..., presets=..., settings=..., more=...)   # right icons, by role

Mac (mac_shell's LAYOUT RULES): `action` goes in the title bar (page.mac_bar); row 1 is
first_row() with `left`, `middle`, then row_icons(); the layout gets CONTENT_MARGINS.
Classic, same structure: row 1 = `action`, `left`, `middle`, the same right icons in the same
order and grid, with the Classic metrics (tool_metrics, 8 px gaps).

Pages never place these widgets themselves and never branch on the interface for them."""
from PySide6.QtWidgets import QHBoxLayout

from . import theme

CLASSIC_SPACING = 8


def page_frame(page, layout, *, action=(), left=(), middle=None, tools=(), recent=None, presets=None,
               settings=None, more=None, stretch_action=None, segments=(), area=None):
    """Lay out `page`'s title action and row 1 into `layout` (the content's QVBoxLayout, still
    empty or holding only what goes above row 1). Returns row 1's QHBoxLayout.
    stretch_action: the `action` widget that takes the title bar's free room (Mac), e.g. a readout.
    segments: buttons that show icons only while row 1 doesn't fit `area` (default: the page;
    mac_shell rule 6), both interfaces."""
    from .mac_shell import CONTENT_MARGINS, first_row, page_bar, row_icons
    mac = theme.is_mac()
    row = QHBoxLayout()
    if mac:
        layout.setContentsMargins(*CONTENT_MARGINS)
        first_row(row)
        if action:
            page.mac_bar, bar = page_bar()
            for w in action:
                bar.addWidget(w, 100 if w is stretch_action else 0)
            bar.addStretch(1)  # the action stays at the left when the readout is empty
    else:
        row.setSpacing(CLASSIC_SPACING)
        for w in action:
            row.addWidget(w, 1 if w is stretch_action else 0)
    for w in left:
        row.addWidget(w)
    if middle is not None:
        middle.setMinimumWidth(1)  # a stretch label gives way first (it elides); 0 would mean "unset"
        row.addWidget(middle, 1)
    else:
        row.addStretch(1)
    row_icons(row, tools=tools, recent=recent, presets=presets, settings=settings, more=more)
    layout.addLayout(row)
    if segments:
        from .mac_shell import fit_row
        page._row_fit = fit_row(area or page, row, segments)
    return row
