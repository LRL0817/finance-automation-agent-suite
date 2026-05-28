"""Compatibility exports for transfer-form helpers."""
from .bank_fields import (
    click_field_dropdown,
    click_match_fill_button,
    dump_bank_debug_controls,
    is_bank_panel_open,
    pick_branch_option,
)
from .ui_helpers import (
    click_control_by_name,
    click_next_step,
    click_submit,
    fill_field_by_label,
    scroll_to_form_top,
    scroll_to_bank_section,
    select_combobox_option,
)

__all__ = [
    "click_control_by_name",
    "click_field_dropdown",
    "click_match_fill_button",
    "click_next_step",
    "click_submit",
    "dump_bank_debug_controls",
    "fill_field_by_label",
    "is_bank_panel_open",
    "pick_branch_option",
    "scroll_to_form_top",
    "scroll_to_bank_section",
    "select_combobox_option",
]
