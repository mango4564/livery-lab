"""A native combobox whose open list previews rows on pointer movement."""
from tkinter import ttk


class HoverChoice(ttk.Combobox):
    def __init__(self, parent, *, on_hover, **kwargs):
        super().__init__(parent, **kwargs)
        self.on_hover = on_hover
        self._last_hover = None
        self._close_after = None
        self._popup = self.tk.call('ttk::combobox::PopdownWindow', self)
        self._listbox = f'{self._popup}.f.l'
        motion = self.register(self._motion)
        leave = self.register(self._schedule_close)
        self.tk.call('bind', self._listbox, '<Motion>', f'{motion} %x %y')
        self.tk.call('bind', self._listbox, '<Leave>', leave)
        self.bind('<Enter>', self._open)
        self.bind('<Leave>', self._schedule_close)

    def _open(self, _event=None):
        if self.instate(['disabled']) or not self['values']:
            return
        self._last_hover = None
        self.tk.call('ttk::combobox::Post', self)

    def _motion(self, x, y):
        x, y = int(x), int(y)
        index = int(self.tk.call(self._listbox, 'nearest', y))
        box = self.tk.call(self._listbox, 'bbox', index)
        if not box:
            return
        _, top, _, height = map(int, box)
        if not top <= y < top + height or index == self._last_hover:
            return
        self._last_hover = index
        self.tk.call(self._listbox, 'selection', 'clear', 0, 'end')
        self.tk.call(self._listbox, 'selection', 'set', index)
        self.tk.call(self._listbox, 'activate', index)
        self.current(index)
        self.on_hover()

    def _schedule_close(self, _event=None):
        if self._close_after is not None:
            self.after_cancel(self._close_after)
        self._close_after = self.after(180, self._close_outside)

    def _close_outside(self):
        self._close_after = None
        if not self.winfo_exists():
            return
        x, y = self.winfo_pointerxy()
        for widget in (str(self), self._popup):
            if not int(self.tk.call('winfo', 'ismapped', widget)):
                continue
            left = int(self.tk.call('winfo', 'rootx', widget))
            top = int(self.tk.call('winfo', 'rooty', widget))
            width = int(self.tk.call('winfo', 'width', widget))
            height = int(self.tk.call('winfo', 'height', widget))
            if left <= x < left + width and top <= y < top + height:
                return
        self.tk.call('ttk::combobox::Unpost', self)

    def destroy(self):
        if self._close_after is not None:
            self.after_cancel(self._close_after)
        super().destroy()
