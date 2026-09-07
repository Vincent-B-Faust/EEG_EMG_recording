from unittest.mock import Mock
from pathlib import Path
import tkinter as tk
import queue

from eeg_emg_recording.gui import RecorderGui


def test_active_recording_rejects_config_save():
    gui = RecorderGui.__new__(RecorderGui)
    gui._run_active = True
    gui.status_var = Mock()
    gui._config_from_fields = Mock(side_effect=AssertionError('must not read new settings'))
    assert gui._save() is False


def test_failed_load_keeps_previous_config_path(monkeypatch):
    gui = RecorderGui.__new__(RecorderGui)
    gui._run_active = False
    gui.config_path = Path('old.yaml')
    gui.config = sentinel = object()
    monkeypatch.setattr('tkinter.filedialog.askopenfilename', lambda **kw: 'missing.yaml')
    monkeypatch.setattr('tkinter.messagebox.showerror', lambda *a: None)
    monkeypatch.setattr('eeg_emg_recording.gui.load_config', Mock(side_effect=ValueError('invalid')))
    gui._choose_config()
    assert gui.config is sentinel and gui.config_path == Path('old.yaml')


def test_finished_waits_for_worker_and_gui_build_does_not_open_hardware(monkeypatch):
    monkeypatch.setattr(RecorderGui, '_refresh_devices', lambda *a, **kw: None)
    root = tk.Tk()
    root.withdraw()
    try:
        gui = RecorderGui(root, config_path=None)
        gui._run_active = True
        gui.worker = Mock()
        gui.worker.is_alive.return_value = True
        gui._form_lock.acquire(gui._setup_roots)
        gui._recording_finished()
        assert gui._run_active and gui._form_lock.active
        gui.worker.is_alive.return_value = False
        gui._recording_finished()
        assert not gui._run_active and not gui._form_lock.active
        assert gui.device_box.instate(['readonly', '!disabled'])
    finally:
        root.destroy()


def test_poll_does_not_redraw_unchanged_signal():
    gui = RecorderGui.__new__(RecorderGui)
    gui.preview_queue, gui.result_queue = queue.Queue(), queue.Queue()
    gui._run_active = gui._close_when_done = False
    gui.preview_buffer = None
    gui.worker = None
    gui.root = Mock()
    gui._draw_preview = Mock()
    gui._poll_queues()
    gui._poll_queues()
    gui._draw_preview.assert_not_called()
    gui.preview_queue.put(object())
    gui._poll_queues()
    gui._draw_preview.assert_called_once()
