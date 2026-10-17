from pathlib import Path


CSS = Path("audiobook_generator/ui/styles.css")


def test_responsive_css_covers_required_width_classes_and_accessibility():
    css = CSS.read_text(encoding="utf-8")
    assert "@media (max-width: 599px)" in css
    assert "@media (min-width: 600px) and (max-width: 839px)" in css
    assert "@media (min-width: 840px)" in css
    assert "min-height: 48px" in css
    assert "overflow-wrap" in css
    assert "max-width" in css
    assert ".settings-row { flex-direction: column !important; }" in css
    assert "min-width: 0 !important" in css
