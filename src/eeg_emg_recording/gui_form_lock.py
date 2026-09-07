"""Lock setup controls while a worker owns an immutable run configuration."""
from __future__ import annotations


class FormLock:
    """Disable inputs, retain pre-existing disabled/readonly states, keep tabs visible."""

    _classes = {"TEntry", "TCombobox", "TButton", "TCheckbutton", "TRadiobutton",
                "TSpinbox", "Entry", "Text", "Spinbox", "Button", "Checkbutton"}

    def __init__(self):
        self._saved = []

    @property
    def active(self):
        return bool(self._saved)

    def acquire(self, roots, exclude=()):
        if self._saved:
            return
        excluded = {id(widget) for widget in exclude}
        def visit(widget):
            if id(widget) in excluded:
                return
            if widget.winfo_class() in self._classes:
                if hasattr(widget, "state"):
                    disabled = widget.instate(["disabled"])
                    self._saved.append((widget, True, disabled))
                    widget.state(["disabled"])
                else:
                    self._saved.append((widget, False, widget.cget("state")))
                    widget.configure(state="disabled")
            for child in widget.winfo_children():
                visit(child)
        for root in roots:
            visit(root)

    def release(self):
        saved, self._saved = self._saved, []
        for widget, ttk, previous in saved:
            if widget.winfo_exists():
                if ttk:
                    widget.state(["disabled" if previous else "!disabled"])
                else:
                    widget.configure(state=previous)
