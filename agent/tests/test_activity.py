from __future__ import annotations

from remotectrl_agent.core.activity import ActivityCapture


def test_keyboard_text_is_flushed_when_active_window_changes() -> None:
    emitted: list[dict] = []
    capture = ActivityCapture(lambda _event, data: emitted.append(data))
    capture.active = True
    editor = {"pid": 10, "process": "editor.exe", "title": "Editor"}
    browser = {"pid": 20, "process": "browser.exe", "title": "Browser"}

    capture._append_text("hello", editor)
    capture._append_text("hi", browser)
    capture._flush_text("test_complete")

    text_events = [event for event in emitted if event["type"] == "keyboard.text"]
    assert [event["detail"]["text"] for event in text_events] == ["hello", "hi"]
    assert text_events[0]["detail"]["boundary"] == "window_changed"


def test_keyboard_text_flushes_on_mouse_click() -> None:
    emitted: list[dict] = []
    capture = ActivityCapture(lambda _event, data: emitted.append(data))
    capture.active = True
    capture._append_text("draft", {"pid": 1, "process": "notes.exe", "title": "Notes"})
    capture._flush_text("mouse_click")

    assert emitted[-1]["type"] == "keyboard.text"
    assert emitted[-1]["detail"] == {"text": "draft", "window": {"pid": 1, "process": "notes.exe", "title": "Notes"}, "boundary": "mouse_click"}