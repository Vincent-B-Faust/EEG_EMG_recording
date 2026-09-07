import tkinter as tk
from tkinter import ttk

from eeg_emg_recording.gui_form_lock import FormLock


def test_form_lock_preserves_readonly_and_original_disabled_controls():
    root = tk.Tk()
    root.withdraw()
    try:
        frame = ttk.Frame(root)
        box = ttk.Combobox(frame, state="readonly")
        inactive = ttk.Button(frame, state="disabled")
        stop = ttk.Button(frame)
        lock = FormLock()
        lock.acquire([frame], exclude=[stop])
        lock.acquire([frame], exclude=[stop])
        assert box.instate(["disabled", "readonly"])
        assert inactive.instate(["disabled"])
        assert not stop.instate(["disabled"])
        lock.release()
        assert box.instate(["readonly", "!disabled"])
        assert inactive.instate(["disabled"])
        lock.release()
    finally:
        root.destroy()
