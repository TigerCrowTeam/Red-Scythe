"""Optional UI-extra smoke test; no radio imports, devices or display are required."""

import asyncio

import pytest


def test_textual_extra_supports_headless_keyboard_navigation():
    pytest.importorskip("textual", reason="install .[ui] to check the optional terminal dependency")
    from textual.app import App, ComposeResult
    from textual.widgets import Input, Select

    class DependencySmoke(App):
        def compose(self) -> ComposeResult:
            yield Input(id="rate")
            yield Select(
                [(name.upper(), name) for name in ("bpsk", "2fsk", "msk", "gmsk", "qpsk")],
                id="modulation",
            )

    async def exercise():
        async with DependencySmoke().run_test(size=(80, 24)) as pilot:
            await pilot.click("#rate")
            await pilot.press("2", "5", "0", "tab")
            assert pilot.app.query_one("#rate", Input).value == "250"
            assert pilot.app.query_one("#modulation", Select).has_focus

    asyncio.run(exercise())
