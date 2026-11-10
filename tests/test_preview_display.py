from copy import deepcopy
import numpy as np
import pytest
from eeg_emg_recording.config import load_config, parse_config, save_config
from eeg_emg_recording.preview import filter_for_preview, preview_range


def test_coupling_offset_and_short_window(make_config):
    config = load_config(make_config())
    values = np.array([4., 5., 6.])
    original = values.copy()
    config.preview.coupling = "DC"
    np.testing.assert_array_equal(filter_for_preview(values, "EEG", 200, config), values)
    config.preview.coupling = "AC"
    np.testing.assert_allclose(filter_for_preview(values, "EEG", 200, config), [-1, 0, 1])
    np.testing.assert_array_equal(values, original)


def test_fixed_range_roundtrip(make_config, tmp_path):
    config = load_config(make_config())
    raw = deepcopy(config.raw)
    raw["preview"].update(coupling="DC", y_range=[-500, 500])
    config = parse_config(raw, config_path=config.config_path)
    assert preview_range(np.array([-1e6, 1e6]), config) == (-500, 500)
    path = tmp_path / "saved.yaml"
    save_config(config, path)
    saved = load_config(path)
    assert saved.preview.coupling == "DC"
    assert saved.preview.y_range == (-500, 500)


@pytest.mark.parametrize("limits", [[2, 1], [1, 1], [float("nan"), 5], [0, float("inf")], [1], "bad"])
def test_invalid_range(make_config, limits):
    config = load_config(make_config())
    config.raw["preview"]["y_range"] = limits
    with pytest.raises(ValueError):
        parse_config(config.raw, config_path=config.config_path)


def test_gui_live_apply_is_separate_and_validated(monkeypatch):
    import tkinter as tk
    from eeg_emg_recording.gui import RecorderGui
    monkeypatch.setattr(RecorderGui, "_refresh_devices", lambda *a, **kw: None)
    root = tk.Tk()
    root.withdraw()
    try:
        gui = RecorderGui(root, config_path=None)
        original = gui.config.preview.coupling
        gui._form_lock.acquire(gui._setup_roots)
        gui.coupling_var.set("DC")
        gui.fixed_y_var.set(True)
        gui.y_min_var.set("-100")
        gui.y_max_var.set("200")
        gui._apply_preview_settings()
        assert gui._preview_config.preview.coupling == "DC"
        assert gui._preview_config.preview.y_range == (-100, 200)
        assert gui.config.preview.coupling == original
        assert gui._config_from_fields().preview.y_range == (-100, 200)
        gui.y_min_var.set("nan")
        with pytest.raises(ValueError):
            gui._read_preview_settings()
    finally:
        root.destroy()
