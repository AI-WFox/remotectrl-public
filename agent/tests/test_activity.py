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
    window = {"pid": 1, "process": "notes.exe", "title": "Notes"}
    capture._append_text("draft", window)
    capture._flush_text("mouse_click")

    assert emitted[-1]["type"] == "keyboard.text"
    assert emitted[-1]["detail"]["text"] == "draft"
    assert emitted[-1]["detail"]["window"] == window
    assert emitted[-1]["detail"]["boundary"] == "mouse_click"
    assert isinstance(emitted[-1]["detail"]["segment_id"], str)


def test_backspace_keeps_typing_in_one_realtime_text_segment() -> None:
    emitted: list[dict] = []
    capture = ActivityCapture(lambda _event, data: emitted.append(data))
    capture.active = True
    window = {"pid": 1, "process": "notes.exe", "title": "Notes"}

    capture._append_text("hellox", window)
    capture._erase_text(window)
    capture._append_text("!", window)

    drafts = [event for event in emitted if event["type"] == "keyboard.text.draft"]
    assert [event["detail"]["text"] for event in drafts] == ["hellox", "hello", "hello!"]
    assert len({event["detail"]["segment_id"] for event in drafts}) == 1
    assert capture.export() == []

    capture._flush_text("test_complete")

    exported = capture.export()
    assert [event["type"] for event in exported] == ["keyboard.text"]
    assert exported[0]["detail"]["text"] == "hello!"
    assert exported[0]["detail"]["segment_id"] == drafts[-1]["detail"]["segment_id"]
    assert not any(event["type"] == "keyboard.key" and event["detail"].get("key") == "Backspace" for event in emitted)
