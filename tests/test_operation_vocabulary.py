"""Characterization tests for the UIA pattern -> operation mapping (Phase A of
docs/plans/finitact-desktop-generalization.md). Platform-inert: no live Windows/UIA needed.
"""

from finitact.operation_vocabulary import (
    EXPAND_COLLAPSE_PATTERN,
    INVOKE_PATTERN,
    OPEN_CONTEXT_MENU,
    RANGE_VALUE_PATTERN,
    SELECTION_ITEM_PATTERN,
    TOGGLE_PATTERN,
    VALUE_PATTERN,
    is_candidate_visible,
    operations_for,
)


def test_a_button_with_only_invoke_pattern_yields_click():
    assert operations_for({INVOKE_PATTERN}) == ("click",)


def test_a_text_field_with_only_value_pattern_yields_fill():
    assert operations_for({VALUE_PATTERN}) == ("fill",)


def test_a_checkbox_with_toggle_pattern_yields_toggle():
    assert operations_for({TOGGLE_PATTERN}) == ("toggle",)


def test_a_list_item_with_selection_item_pattern_yields_select():
    assert operations_for({SELECTION_ITEM_PATTERN}) == ("select",)


def test_a_slider_with_range_value_pattern_yields_set_range():
    assert operations_for({RANGE_VALUE_PATTERN}) == ("set_range",)


def test_a_tree_node_with_expand_collapse_pattern_yields_expand_collapse():
    assert operations_for({EXPAND_COLLAPSE_PATTERN}) == ("expand_collapse",)


def test_an_editable_combobox_yields_both_fill_and_expand_collapse_not_a_collapsed_guess():
    # Mirrors snapshot.js emitting a separate 'fill' and 'click "Open X"' candidate for the
    # same editable-combobox element (finitact/snapshot.js:118-133): don't pick one for it.
    ops = operations_for({VALUE_PATTERN, EXPAND_COLLAPSE_PATTERN})
    assert set(ops) == {"fill", "expand_collapse"}


def test_a_control_with_no_recognized_pattern_yields_no_operation():
    # Caller must treat this as unsupported_control (ADR-0006), not guess or drop silently.
    assert operations_for(set()) == ()
    assert operations_for({"SomeVendorSpecificPatternIdentifiers.Pattern"}) == ()


def test_output_order_is_deterministic_regardless_of_input_set_order():
    a = operations_for({TOGGLE_PATTERN, INVOKE_PATTERN})
    b = operations_for({INVOKE_PATTERN, TOGGLE_PATTERN})
    assert a == b


def test_open_context_menu_is_never_derived_from_any_known_pattern_combination():
    # It has no supporting pattern; a concrete adapter must attach it as its own policy,
    # `operations_for` must never invent it (finitact/action_adapter.py's context-menu note).
    all_patterns = {
        INVOKE_PATTERN,
        VALUE_PATTERN,
        TOGGLE_PATTERN,
        SELECTION_ITEM_PATTERN,
        EXPAND_COLLAPSE_PATTERN,
        RANGE_VALUE_PATTERN,
    }
    assert OPEN_CONTEXT_MENU not in operations_for(all_patterns)


def test_an_enabled_onscreen_element_is_visible():
    assert is_candidate_visible(is_enabled=True, is_offscreen=False)


def test_a_disabled_element_is_not_visible_even_if_onscreen():
    assert not is_candidate_visible(is_enabled=False, is_offscreen=False)


def test_an_offscreen_element_is_not_visible_even_if_enabled():
    # This is the case the Notepad prototype's IsEnabled-only check missed
    # (docs/evaluations/windows-adapter-prototype/prototype-lib.ps1).
    assert not is_candidate_visible(is_enabled=True, is_offscreen=True)
