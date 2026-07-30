"""NI-DAQmx EEG/EMG recording and thermal-session synchronization."""

from .config import load_config
from .recorder import Recorder, RunResult, run_recording

__all__ = ["Recorder", "RunResult", "load_config", "run_recording"]
__version__ = "0.1.0"
