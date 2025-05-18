from collections import defaultdict

class EventEmitter:
    def __init__(self):
        self._events = defaultdict(list)

    def on(self, event_name, listener):
        """Register a listener for an event."""
        self._events[event_name].append(listener)

    def emit(self, event_name, *args, **kwargs):
        """Emit an event and call all listeners."""
        for listener in self._events[event_name]:
            listener(*args, **kwargs)

# Create a global instance of the event emitter
event_emitter = EventEmitter()