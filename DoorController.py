import datetime
import re
from typing import Any, Optional

import appdaemon.plugins.hass.hassapi as hass

# Constants for relay toggle timeout
RELAY_TOGGLE_TIMEOUT = 0.5


class DoorController(hass.Hass):
    """
    DoorController is an AppDaemon app that manages the state of a door using sensors and a switch.
    It listens for state changes in door sensors, controls a relay to open or close the door,
    and updates Home Assistant entities to reflect the door status and app health.
    """

    def initialize(self) -> None:
        """
        Initialize the DoorController app.
        - Load configuration parameters.
        - Set initial state.
        - Register state listeners and services.
        - Create and initialize door status and health entities.
        """
        self.log("Initializing Door Controller App")
        self.friendly_name = self.args.get("friendly_name", "Door Controller")
        self.entity_prefix = self._slugify(self.args.get("entity_prefix", self.friendly_name))
        self.input_button_open = self._build_generated_entity_id("input_button", "open")
        self.input_button_close = self._build_generated_entity_id("input_button", "close")
        self.input_button_external = self._build_generated_entity_id(
            "input_button", "external"
        )
        self.door_status_sensor = self._build_generated_entity_id("sensor", "status")
        self.health_status_sensor = self._build_generated_entity_id("sensor", "health")

        required_args = (
            "door_relay",
        )
        missing_args = [key for key in required_args if key not in self.args]
        if missing_args:
            self.log(
                f"Configuration error: missing required keys: {', '.join(missing_args)}",
                level="ERROR",
            )
            if self.health_status_sensor:
                self.create_health_entity()
                self.update_health_entity("faulty")
            return

        self.door_relay = self.args["door_relay"]

        self.close_sensor = self.args.get("close_sensor")
        self.open_sensor = self.args.get("open_sensor")
        self.close_sensor_active_state = self._normalize_state(
            self.args.get("close_sensor_active_state", "off")
        )
        self.open_sensor_active_state = self._normalize_state(
            self.args.get("open_sensor_active_state", "off")
        )
        self.timeout = int(self.args.get("timeout", 30))

        self.has_sensors = bool(self.close_sensor or self.open_sensor)
        self.isSensorless = not self.has_sensors
        self.door_state = "unknown"
        self.last_action_time: Optional[datetime.datetime] = None
        self.pending_target: Optional[str] = None
        self.diagnostic_handle: Optional[str] = None

        if self.close_sensor:
            self.listen_state(self.door_status_changed, self.close_sensor)
        if self.open_sensor:
            self.listen_state(self.door_status_changed, self.open_sensor)

        self.listen_state(self.handle_open_event, self.input_button_open)
        self.listen_state(self.handle_close_event, self.input_button_close)
        self.listen_state(self.handle_external_button_event, self.input_button_external)

        self.create_command_entities()
        self.create_door_status_entity()
        self.create_health_entity()
        self.update_health_entity("healthy")

        if self.has_sensors:
            self.door_status_changed(None, None, None, None, None)

        self.log(f"{self.friendly_name} Initialized")

    def create_command_entities(self) -> None:
        """
        Create app-managed command helper entities.
        """
        self.set_state(
            self.input_button_open,
            state="idle",
            attributes={"friendly_name": f"{self.friendly_name} Open"},
        )
        self.set_state(
            self.input_button_close,
            state="idle",
            attributes={"friendly_name": f"{self.friendly_name} Close"},
        )
        self.set_state(
            self.input_button_external,
            state="idle",
            attributes={"friendly_name": f"{self.friendly_name} External"},
        )

    def create_door_status_entity(self) -> None:
        """
        Create a sensor entity for the door status.
        """
        self.update_door_status_entity("unknown")

    def update_door_status_entity(self, state: str) -> None:
        """
        Update the state of the door status entity.

        :param state: The new state of the door status.
        """
        self.set_state(
            self.door_status_sensor,
            state=state,
            attributes={"friendly_name": f"{self.friendly_name} Status"},
        )

    def create_health_entity(self) -> None:
        """
        Create a sensor entity for the app health status.
        """
        self.update_health_entity("unknown")

    def update_health_entity(self, state: str) -> None:
        """
        Update the state of the app health entity.

        :param state: The new state of the app health.
        """
        self.set_state(
            self.health_status_sensor,
            state=state,
            attributes={"friendly_name": f"{self.friendly_name} Health"},
        )

    def handle_open_event(
        self, entity: str, attribute: str, old: str, new: str, kwargs: dict
    ) -> None:
        """
        Handle the input_button to open the door.

        :param entity: The input_button entity that changed state.
        :param attribute: The attribute of the entity that changed.
        :param old: The old state of the entity.
        :param new: The new state of the entity.
        :param kwargs: Additional keyword arguments.
        """

        self.log("Opening door (input_button)...")
        self.request_target_state("open")

    def handle_close_event(
        self, entity: str, attribute: str, old: str, new: str, kwargs: dict
    ) -> None:
        """
        Handle the input_button to close the door.

        :param entity: The input_button entity that changed state.
        :param attribute: The attribute of the entity that changed.
        :param old: The old state of the entity.
        :param new: The new state of the entity.
        :param kwargs: Additional keyword arguments.
        """

        self.log("Closing door (input_button)...")
        self.request_target_state("closed")

    def handle_external_button_event(
        self, entity: str, attribute: str, old: str, new: str, kwargs: dict
    ) -> None:
        """
        Handle the external button event to trigger door action.

        :param entity: The external button entity that changed state.
        :param attribute: The attribute of the entity that changed.
        :param old: The old state of the entity.
        :param new: The new state of the entity.
        :param kwargs: Additional keyword arguments.
        """

        self.log("External button activated, pulsing relay.")
        self.clear_pending_command()
        self.activate_relay()

    def activate_relay(self, _: Any = None) -> None:
        """
        Activate the relay to move the door and set the last action time.
        """
        self.log("Activating relay", level="DEBUG")
        self.turn_on(self.door_relay)
        self.run_in(self.turn_off_switch, RELAY_TOGGLE_TIMEOUT)
        self.last_action_time = datetime.datetime.now()

        if self.has_sensors and self.pending_target:
            self.schedule_diagnostics()

    def turn_off_switch(self, _: Any) -> None:
        """
        Turn off the door switch after activating the relay.
        """
        self.log("Turning off door switch")
        self.turn_off(self.door_relay)

    def request_target_state(self, target_state: str) -> None:
        """
        Request an explicit open/close action with a single relay pulse.
        """
        if self.has_sensors and self.door_state == target_state:
            action = "open" if target_state == "open" else "close"
            self.log(
                f"Ignoring '{action}' request because the door is already {target_state}."
            )
            return

        self.pending_target = target_state if self.has_sensors else None
        self.activate_relay()

    def door_status_changed(
        self, entity: Any, attribute: Any, old: Any, new: Any, kwargs: Any
    ) -> None:
        """
        Handle changes in door sensor states to update the door status.

        :param entity: The sensor entity that changed state.
        :param attribute: The attribute of the sensor that changed.
        :param old: The old state of the sensor.
        :param new: The new state of the sensor.
        :param kwargs: Additional keyword arguments.
        """
        if self.isSensorless:
            return

        target_state = self.pending_target
        state = self.evaluate_door_state()

        if target_state and self.movement_timed_out() and state == "faulty":
            self.log(
                f"Fault detected: Door did not reach '{target_state}' "
                f"within {self.timeout} seconds.",
                level="WARNING",
            )

        self.set_door_state(state)

    def run_diagnostics(self, kwargs: Any) -> None:
        """
        Run diagnostics to check the state of the door sensors and detect faults.

        :param kwargs: Additional keyword arguments.
        """
        self.diagnostic_handle = None

        if self.isSensorless:
            return

        self.set_door_state(self.evaluate_door_state())

    def evaluate_door_state(self) -> str:
        """
        Evaluate the logical door state from the configured sensors.
        """
        close_active = self.get_sensor_active_state(
            self.close_sensor, self.close_sensor_active_state
        )
        open_active = self.get_sensor_active_state(
            self.open_sensor, self.open_sensor_active_state
        )

        if self.close_sensor and self.open_sensor:
            if close_active is None or open_active is None:
                return "unknown"
            if close_active and open_active:
                return "faulty"
            if close_active:
                return "closed"
            if open_active:
                return "open"
            if self.pending_target and self.movement_timed_out():
                return "faulty"
            return "intermediate"

        if self.close_sensor:
            if close_active is None:
                return "unknown"
            if close_active:
                return "closed"
            if self.pending_target == "open" and self.movement_timed_out():
                return "open"
            if self.pending_target == "closed" and self.movement_timed_out():
                return "faulty"
            if self.command_in_progress():
                return "intermediate"
            return "open"

        if self.open_sensor:
            if open_active is None:
                return "unknown"
            if open_active:
                return "open"
            if self.pending_target == "closed" and self.movement_timed_out():
                return "closed"
            if self.pending_target == "open" and self.movement_timed_out():
                return "faulty"
            if self.command_in_progress():
                return "intermediate"
            return "closed"

        return "unknown"

    def set_door_state(self, state: str) -> None:
        """
        Persist the door state.
        """
        previous_state = self.door_state
        self.door_state = state
        self.update_door_status_entity(state)

        if state != previous_state:
            self.log(f"Door state changed: {previous_state} -> {state}")

        if state in {"closed", "open"}:
            self.clear_pending_command()
        elif state == "faulty" and (
            self.pending_target is None or self.movement_timed_out()
        ):
            self.clear_pending_command()

    def get_sensor_active_state(
        self, sensor_entity: Optional[str], active_state: str
    ) -> Optional[bool]:
        """
        Return whether the configured sensor currently reports its active state.
        """
        if not sensor_entity:
            return None

        state = self.get_state(sensor_entity)
        if state is None:
            return None

        normalized_state = self._normalize_state(state)
        if normalized_state in {"unknown", "unavailable", "none"}:
            return None

        return normalized_state == active_state

    def schedule_diagnostics(self) -> None:
        """
        Schedule a timeout check from the most recent relay activation.
        """
        self.cancel_diagnostics()
        self.diagnostic_handle = self.run_in(self.run_diagnostics, self.timeout)

    def cancel_diagnostics(self) -> None:
        """
        Cancel any pending diagnostics timer.
        """
        if self.diagnostic_handle is None:
            return

        self.cancel_timer(self.diagnostic_handle)
        self.diagnostic_handle = None

    def command_in_progress(self) -> bool:
        """
        Return True while an app-issued movement is still within its timeout window.
        """
        return self.pending_target is not None and not self.movement_timed_out()

    def clear_pending_command(self) -> None:
        """
        Clear the active command and any pending diagnostics.
        """
        self.pending_target = None
        self.cancel_diagnostics()

    def movement_timed_out(self) -> bool:
        """
        Return True when the latest door action exceeded the configured timeout.
        """
        if self.last_action_time is None:
            return False

        return (
            datetime.datetime.now() - self.last_action_time
        ).total_seconds() >= self.timeout

    def _normalize_state(self, state: Any) -> str:
        """
        Normalize Home Assistant/AppDaemon state values for comparisons.
        """
        return str(state).strip().lower()

    def _slugify(self, value: Any) -> str:
        """
        Convert a label into a stable entity-id suffix.
        """
        slug = re.sub(r"[^a-z0-9]+", "_", str(value).strip().lower()).strip("_")
        return slug or "door_controller"

    def _build_generated_entity_id(self, domain: str, suffix: str) -> str:
        """
        Build an app-managed entity id from the configured prefix.
        """
        return f"{domain}.{self.entity_prefix}_{suffix}"
