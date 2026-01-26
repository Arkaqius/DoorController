from typing import Any
import appdaemon.plugins.hass.hassapi as hass
import datetime
from collections import namedtuple

# Constants for relay toggle timeout
RELAY_TOGGLE_TIMEOUT = 0.5

# Named tuple for door status
DoorStatus = namedtuple("DoorStatus", ["state"])


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

        # Get configuration from app.yaml
        try:
            self.friendly_name = self.args["friendly_name"]
            self.health_status_sensor = self.args["health_status_sensor"]
            self.door_switch = self.args["door_switch"]
            self.door_status_sensor = self.args["door_status_sensor"]

            self.input_button_open = self.args["input_button_open"]
            self.input_button_close = self.args["input_button_close"]
            self.input_button_external = self.args["external_button"]
            self.input_button_pedestrian = self.args.get("input_button_pedestrian")

            self.close_sensor = self.args.get("close_sensor")
            self.open_sensor = self.args.get("open_sensor")
            self.timeout = self.args.get(
                "timeout", 30
            )  # Default timeout if not specified
            self.pedestrian_open_timeout = self.args.get(
                "pedestrian_open_timeout", 10
            )  # Default pedestrian open timeout if not specified

            self.door_state = DoorStatus(
                "unknown"
            )  # Possible states: "closed", "open", "intermediate", "faulty"
            self.last_action_time = None
            self.last_command_by_app = False
            self.predict_mov_dir = "open"
            self.isSensorless: bool = not (self.close_sensor and self.open_sensor)

            # Listen for state changes in sensors if configured
            if self.close_sensor:
                self.listen_state(self.door_status_changed, self.close_sensor)
            if self.open_sensor:
                self.listen_state(self.door_status_changed, self.open_sensor)

            # Listen for input_button presses
            self.listen_state(self.handle_open_event, self.input_button_open)
            self.listen_state(self.handle_close_event, self.input_button_close)

            # Listen for external button state changes
            self.listen_state(
                self.handle_external_button_event, self.input_button_external
            )

            # Listen for pedestrian button press if configured
            if self.input_button_pedestrian:
                self.listen_state(
                    self.handle_pedestrian_event, self.input_button_pedestrian
                )

            # Create and initialize entities
            self.create_door_status_entity()
            self.update_door_status_entity("unknown")
            self.create_health_entity()
            self.update_health_entity("healthy")

            self.log(f"{self.friendly_name} Initialized")
        except KeyError as e:
            self.log(f"Configuration error: {e}")
            self.create_health_entity()
            self.update_health_entity("faulty")

        # Force diagnostic to determine current door status if sensors are available
        if self.close_sensor or self.open_sensor:
            self.door_status_changed(None, None, None, None, None)

    def create_door_status_entity(self) -> None:
        """
        Create a sensor entity for the door status.
        """
        self.set_state(
            self.door_status_sensor,
            state="unknown",
            attributes={"friendly_name": f"{self.friendly_name} Status"},
        )

    def update_door_status_entity(self, state: str) -> None:
        """
        Update the state of the door status entity.

        :param state: The new state of the door status.
        """
        self.set_state(self.door_status_sensor, state=state)

    def create_health_entity(self) -> None:
        """
        Create a sensor entity for the app health status.
        """
        self.set_state(
            self.health_status_sensor,
            state="unknown",
            attributes={"friendly_name": f"{self.friendly_name} Health"},
        )

    def update_health_entity(self, state: str) -> None:
        """
        Update the state of the app health entity.

        :param state: The new state of the app health.
        """
        self.set_state(self.health_status_sensor, state=state)

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
        if not self.isSensorless:
            if self.predict_mov_dir == "open":
                self.activate_relay()  # TODO Opaque func is needed
                self.predict_mov_dir == "close"
            else:
                self.activate_relay()
                self.run_in(self.activate_relay, 1)
                self.run_in(self.activate_relay, 2)
                self.predict_mov_dir == "close"
        else:
            self.activate_relay()
        self.last_command_by_app = True

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
        if not self.isSensorless:
            if self.predict_mov_dir == "close":
                self.activate_relay()  # TODO Opaque func is needed
                self.predict_mov_dir == "open"
            else:
                self.activate_relay()
                self.run_in(self.activate_relay, 1)
                self.run_in(self.activate_relay, 2)
                self.predict_mov_dir == "open"
        else:
            self.activate_relay()
        self.last_command_by_app = True

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
        self.log("External button event detected...")
        if new != "unknown":
            self.log("External button activated, performing door action.")
            self.activate_relay()
            self.last_command_by_app = True

    def handle_pedestrian_event(
        self, entity: str, attribute: str, old: str, new: str, kwargs: dict
    ) -> None:
        """
        Handle the input_button to open the door for pedestrian access.

        :param entity: The input_button entity that changed state.
        :param attribute: The attribute of the entity that changed.
        :param old: The old state of the entity.
        :param new: The new state of the entity.
        :param kwargs: Additional keyword arguments.
        """
        self.log("Pedestrian access requested (input_button)...")
        self.activate_relay()
        self.run_in(self.activate_relay, self.pedestrian_open_timeout)
        self.last_command_by_app = Truef
    def activate_relay(self, _: Any = None) -> None:
        """
        Activate the relay to move the door and set the last action time.
        """
        self.log("activate_relay", level="DEBUG")
        self.turn_on(self.door_switch)
        self.run_in(self.turn_off_switch, RELAY_TOGGLE_TIMEOUT)
        self.last_action_time: datetime.datetime = datetime.datetime.now()

    def turn_off_switch(self, _: Any) -> None:
        """
        Turn off the door switch after activating the relay.
        """
        self.log("Turning off door switch")
        self.turn_off(self.door_switch)

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
            self.log("Sensors not configured, skipping state update.")
            return

        close_sensor_state = self.get_state(self.close_sensor)
        open_sensor_state = self.get_state(self.open_sensor)

        if close_sensor_state == "off" and open_sensor_state == "on":
            self.door_state = DoorStatus("closed")
            self.update_door_status_entity("closed")
            self.predict_mov_dir = "open"
            self.log("Door is fully closed")
        elif close_sensor_state == "on" and open_sensor_state == "off":
            self.door_state = DoorStatus("open")
            self.update_door_status_entity("open")
            self.predict_mov_dir = "close"
            self.log("Door is fully open")
        elif close_sensor_state == "off" and open_sensor_state == "off":
            self.log(
                "Fault detected: Both sensors indicate the door is fully closed and fully open."
            )
            self.update_door_status_entity("faulty")
        else:
            # Both sensors are on, indicating an intermediate state
            self.door_state = DoorStatus("intermediate")
            self.update_door_status_entity("intermediate")
            self.log("Door is in intermediate state")

            # Schedule diagnostics if the last command was from the app
            if self.last_command_by_app:
                self.run_in(self.run_diagnostics, self.timeout)

    def run_diagnostics(self, kwargs: Any) -> None:
        """
        Run diagnostics to check the state of the door sensors and detect faults.

        :param kwargs: Additional keyword arguments.
        """
        if self.isSensorless:
            self.log("Sensors not configured, skipping diagnostics.")
            return

        close_sensor_state = self.get_state(self.close_sensor)
        open_sensor_state = self.get_state(self.open_sensor)
        current_time = datetime.datetime.now()

        # Check if both sensors are "on" (intermediate state)
        if close_sensor_state == "on" and open_sensor_state == "on":
            if self.last_command_by_app:
                # Check if the door movement timed out
                if (
                    current_time - self.last_action_time
                ).total_seconds() > self.timeout:
                    self.log("Fault detected: Door operation timeout.")
                    self.door_state = DoorStatus("faulty")
                    self.update_door_status_entity("faulty")
            else:
                # If not controlled by the app, it's just an intermediate state
                self.update_door_status_entity("intermediate")

        # Reset the flag after diagnostics
        self.last_command_by_app = False
