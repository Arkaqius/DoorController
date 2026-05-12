import importlib
import pathlib
import sys
import types

import pytest


class FakeHass:
    def __init__(self, *args, **kwargs):
        self.args = {}
        self.state_store = {}
        self.listeners = []
        self.event_listeners = []
        self.service_calls = []
        self.turn_on_calls = []
        self.turn_off_calls = []
        self.run_in_calls = []
        self.cancelled_timers = []
        self.logs = []

    def log(self, message, level="INFO"):
        self.logs.append({"level": level, "message": message})

    def set_state(self, entity, state=None, attributes=None):
        self.state_store[entity] = {
            "state": state,
            "attributes": attributes or {},
        }

    def get_state(self, entity):
        value = self.state_store.get(entity)
        if value is None:
            return None
        return value["state"]

    def listen_state(self, callback, entity):
        self.listeners.append({"callback": callback, "entity": entity})

    def listen_event(self, callback, event=None, **kwargs):
        handle = f"event_{len(self.event_listeners) + 1}"
        self.event_listeners.append(
            {"callback": callback, "event": event, "kwargs": kwargs, "handle": handle}
        )
        return handle

    def call_service(self, service, **kwargs):
        self.service_calls.append({"service": service, "kwargs": kwargs})

    def turn_on(self, entity):
        self.turn_on_calls.append(entity)

    def turn_off(self, entity):
        self.turn_off_calls.append(entity)

    def run_in(self, callback, delay):
        handle = f"timer_{len(self.run_in_calls) + 1}"
        self.run_in_calls.append(
            {"callback": callback, "delay": delay, "handle": handle}
        )
        return handle

    def cancel_timer(self, handle):
        self.cancelled_timers.append(handle)


def _install_fake_appdaemon():
    appdaemon_module = types.ModuleType("appdaemon")
    plugins_module = types.ModuleType("appdaemon.plugins")
    hass_module = types.ModuleType("appdaemon.plugins.hass")
    hassapi_module = types.ModuleType("appdaemon.plugins.hass.hassapi")
    hassapi_module.Hass = FakeHass

    appdaemon_module.plugins = plugins_module
    plugins_module.hass = hass_module
    hass_module.hassapi = hassapi_module

    sys.modules["appdaemon"] = appdaemon_module
    sys.modules["appdaemon.plugins"] = plugins_module
    sys.modules["appdaemon.plugins.hass"] = hass_module
    sys.modules["appdaemon.plugins.hass.hassapi"] = hassapi_module


@pytest.fixture
def controller_class():
    repo_root = pathlib.Path(__file__).resolve().parents[1]
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))

    _install_fake_appdaemon()
    module = importlib.import_module("DoorController")
    return module.DoorController


@pytest.fixture
def make_controller(controller_class):
    def factory(args=None, entity_states=None):
        controller = controller_class()
        controller.args = args or {"door_relay": "switch.door_relay"}

        for entity_id, state in (entity_states or {}).items():
            controller.set_state(entity_id, state=state)

        controller.initialize()
        return controller

    return factory
