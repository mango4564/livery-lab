"""Release offline Tk test fixtures on the Tcl interpreter's owning thread."""
import gc
import threading
import unittest


def _finish_tk_test(test, previous_threads):
    # Registered before setUp, so normal application/widget cleanups run first.
    # A destroyed root is still a live Tcl interpreter while Python widgets or
    # Variables reference it. Never leave those cycles to a data worker's GC.
    for thread in set(threading.enumerate()) - previous_threads:
        thread.join(timeout=3)
        test.assertFalse(thread.is_alive(), f'Test left a worker running: {thread.name}')
    for name in list(vars(test)):
        if not name.startswith('_'):
            delattr(test, name)
    gc.collect()


class TkTestCase(unittest.TestCase):
    def run(self, result=None):
        self.addCleanup(_finish_tk_test, self, set(threading.enumerate()))
        return super().run(result)
