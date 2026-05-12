import datetime
import json
import re
import unicodedata
from typing import Any, Optional

import appdaemon.plugins.hass.hassapi as hass

# Constants for relay toggle timeout
RELAY_TOGGLE_TIMEOUT = 0.5
POLISH_CHAR_REPLACEMENTS = {
    "ą": "a",
    "ć": "c",
    "ę": "e",
    "ł": "l",
    "ń": "n",
    "ó": "o",
    "ś": "s",
    "ź": "z",
    "ż": "z",
    "Ą": "a",
    "Ć": "c",
    "Ę": "e",
    "Ł": "l",
    "Ń": "n",
    "Ó": "o",
    "Ś": "s",
    "Ź": "z",
    "Ż": "z",
    "Ä…": "a",
    "Ä‡": "c",
    "Ä™": "e",
    "Ĺ‚": "l",
    "Ĺ„": "n",
    "Ăł": "o",
    "Ĺ›": "s",
    "Ĺş": "z",
    "ĹĽ": "z",
    "Ä„": "a",
    "Ä†": "c",
    "Ä": "e",
    "Ĺ": "l",
    "Ĺ": "n",
    "Ă“": "o",
    "Ĺš": "s",
    "Ĺą": "z",
    "Ĺ»": "z",
}


class DoorController(hass.Hass):
    """
    DoorController is an AppDaemon app that manages the state of a door using sensors and a switch.
    It listens for state changes in door sensors, controls a relay to open or close the door,
    and publishes Home Assistant entities through MQTT Discovery.
    """

    def initialize(self) -> None:
        """
        Initialize the DoorController app.
        - Load configuration parameters.
        - Register state listeners and MQTT command listeners.
        - Publish MQTT Discovery for command buttons, cover, status sensor, and health sensor.
        """
        self.log("Initializing Door Controller App")
        configured_friendly_name = self.args.get("friendly_name")
        self.friendly_name = configured_friendly_name or "Sterownik drzwi"
        default_entity_prefix = configured_friendly_name or "door_controller"
        self.entity_prefix = self._slugify(
            self.args.get("entity_prefix", default_entity_prefix)
        )

        self.button_open_entity = self._build_generated_entity_id("button", "open")
        self.button_close_entity = self._build_generated_entity_id("button", "close")
        self.button_external_entity = self._build_generated_entity_id(
            "button", "external"
        )
        self.cover_entity = self.args.get(
            "cover_entity_id", f"cover.{self.entity_prefix}"
        )
        self.door_status_sensor = self._build_generated_entity_id("sensor", "status")
        self.health_status_sensor = self._build_generated_entity_id("sensor", "health")

        self.cover_device_class = self.args.get("cover_device_class", "garage")
        self.mqtt_namespace = self.args.get("mqtt_namespace", "mqtt")
        self.mqtt_discovery_prefix = self._normalize_topic(
            self.args.get("mqtt_discovery_prefix", "homeassistant")
        )
        self.mqtt_base_topic = self._normalize_topic(
            self.args.get("mqtt_base_topic", f"door_controller/{self.entity_prefix}")
        )
        self.availability_topic = f"{self.mqtt_base_topic}/availability"
        self.status_state_topic = f"{self.mqtt_base_topic}/status/state"
        self.health_state_topic = f"{self.mqtt_base_topic}/health/state"
        self.cover_state_topic = f"{self.mqtt_base_topic}/cover/state"
        self.cover_command_topic = f"{self.mqtt_base_topic}/cover/command"
        self.button_open_command_topic = f"{self.mqtt_base_topic}/button/open/command"
        self.button_close_command_topic = (
            f"{self.mqtt_base_topic}/button/close/command"
        )
        self.button_external_command_topic = (
            f"{self.mqtt_base_topic}/button/external/command"
        )

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

        required_args = ("door_relay",)
        missing_args = [key for key in required_args if key not in self.args]
        if missing_args:
            self.log(
                f"Configuration error: missing required keys: {', '.join(missing_args)}",
                level="ERROR",
            )
            self.publish_mqtt_discovery(include_commands=False)
            self.create_health_entity()
            self.update_health_entity("faulty")
            return

        self.door_relay = self.args["door_relay"]

        if self.close_sensor:
            self.listen_state(self.door_status_changed, self.close_sensor)
        if self.open_sensor:
            self.listen_state(self.door_status_changed, self.open_sensor)

        self.create_command_entities()
        self.create_door_status_entity()
        self.create_health_entity()
        self.update_health_entity("healthy")

        if self.has_sensors:
            self.door_status_changed(None, None, None, None, None)

        self.log(f"{self.friendly_name} Initialized")

    def create_command_entities(self) -> None:
        """
        Publish MQTT Discovery and subscribe to command topics.
        """
        self.publish_mqtt_discovery()
        self.subscribe_mqtt_commands()

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
        self.publish_mqtt_state(self.status_state_topic, state)

        if self.has_sensors:
            self.publish_mqtt_state(
                self.cover_state_topic, self._door_state_to_cover_state(state)
            )

    def create_health_entity(self) -> None:
        """
        Create a sensor entity for the app health status.
        """
        self.update_health_entity("unknown")

    def update_health_entity(self, state: str) -> None:
        """
        Update the state of the app health.

        :param state: The new state of the app health.
        """
        self.publish_mqtt_state(self.health_state_topic, state)

    def handle_open_event(
        self, entity: str, attribute: str, old: str, new: str, kwargs: dict
    ) -> None:
        """
        Handle the command button to open the door.

        :param entity: The button or cover entity that sent the command.
        :param attribute: The attribute of the entity that changed.
        :param old: The old state of the entity.
        :param new: The new command payload.
        :param kwargs: Additional keyword arguments.
        """

        self.log("Opening door (command button)...")
        self.request_target_state("open")

    def handle_close_event(
        self, entity: str, attribute: str, old: str, new: str, kwargs: dict
    ) -> None:
        """
        Handle the command button to close the door.

        :param entity: The button or cover entity that sent the command.
        :param attribute: The attribute of the entity that changed.
        :param old: The old state of the entity.
        :param new: The new command payload.
        :param kwargs: Additional keyword arguments.
        """

        self.log("Closing door (command button)...")
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

    def handle_mqtt_command_event(
        self, event_name: str, data: dict, kwargs: dict
    ) -> None:
        """
        Handle MQTT button and cover commands published by Home Assistant.
        """
        topic = self._event_topic(data, kwargs)
        payload = self._normalize_payload((data or {}).get("payload"))

        if topic == self.cover_command_topic:
            if payload == "OPEN":
                self.handle_open_event(self.cover_entity, "command", None, payload, {})
            elif payload == "CLOSE":
                self.handle_close_event(self.cover_entity, "command", None, payload, {})
            elif payload == "STOP":
                self.handle_external_button_event(
                    self.cover_entity, "command", None, payload, {}
                )
            else:
                self.log(f"Ignoring unsupported cover MQTT payload: {payload}")
            return

        if topic == self.button_open_command_topic and payload == "PRESS":
            self.handle_open_event(self.button_open_entity, "command", None, payload, {})
            return

        if topic == self.button_close_command_topic and payload == "PRESS":
            self.handle_close_event(
                self.button_close_entity, "command", None, payload, {}
            )
            return

        if topic == self.button_external_command_topic and payload == "PRESS":
            self.handle_external_button_event(
                self.button_external_entity, "command", None, payload, {}
            )
            return

        self.log(f"Ignoring unsupported MQTT command: topic={topic}, payload={payload}")

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

    def publish_mqtt_discovery(self, include_commands: bool = True) -> None:
        """
        Publish retained MQTT Discovery configs for Home Assistant.
        """
        configs = {
            self._discovery_topic("sensor", "status"): self._sensor_payload(
                self.door_status_sensor,
                self._build_friendly_name("Status"),
                "status",
                self.status_state_topic,
                "mdi:garage",
            ),
            self._discovery_topic("sensor", "health"): self._sensor_payload(
                self.health_status_sensor,
                self._build_friendly_name("Stan aplikacji"),
                "health",
                self.health_state_topic,
                "mdi:heart-pulse",
                entity_category="diagnostic",
            ),
        }

        if include_commands:
            configs.update(
                {
                    self._discovery_topic("button", "open"): self._button_payload(
                        self.button_open_entity,
                        self._build_friendly_name("Otwórz"),
                        "open",
                        self.button_open_command_topic,
                        "mdi:arrow-up-bold",
                    ),
                    self._discovery_topic("button", "close"): self._button_payload(
                        self.button_close_entity,
                        self._build_friendly_name("Zamknij"),
                        "close",
                        self.button_close_command_topic,
                        "mdi:arrow-down-bold",
                    ),
                    self._discovery_topic(
                        "button", "external"
                    ): self._button_payload(
                        self.button_external_entity,
                        self._build_friendly_name("Przycisk zewnętrzny"),
                        "external",
                        self.button_external_command_topic,
                        "mdi:gesture-tap-button",
                    ),
                    self._discovery_topic("cover", "cover"): self._cover_payload(),
                }
            )

        for topic, payload in configs.items():
            self.publish_mqtt_payload(topic, payload, retain=True)

        self.publish_mqtt_state(self.availability_topic, "online")

    def subscribe_mqtt_commands(self) -> None:
        """
        Subscribe AppDaemon's MQTT plugin to Home Assistant command topics.
        """
        command_topics = (
            self.cover_command_topic,
            self.button_open_command_topic,
            self.button_close_command_topic,
            self.button_external_command_topic,
        )

        for topic in command_topics:
            try:
                self.call_service(
                    "mqtt/subscribe", topic=topic, namespace=self.mqtt_namespace
                )
            except Exception as error:
                self.log(
                    f"MQTT subscribe failed for {topic}: {error}", level="ERROR"
                )

            self.listen_event(
                self.handle_mqtt_command_event,
                "MQTT_MESSAGE",
                topic=topic,
                namespace=self.mqtt_namespace,
            )

    def publish_mqtt_state(self, topic: str, state: str) -> None:
        """
        Publish a retained MQTT state payload.
        """
        self.publish_mqtt_payload(topic, state, retain=True)

    def publish_mqtt_payload(self, topic: str, payload: Any, retain: bool) -> None:
        """
        Publish an MQTT payload through the AppDaemon MQTT plugin.
        """
        if isinstance(payload, (dict, list)):
            payload = json.dumps(payload, ensure_ascii=False)

        try:
            self.call_service(
                "mqtt/publish",
                topic=topic,
                payload=payload,
                retain=retain,
                namespace=self.mqtt_namespace,
            )
        except Exception as error:
            self.log(f"MQTT publish failed for {topic}: {error}", level="ERROR")

    def _base_discovery_payload(
        self, entity_id: str, name: str, unique_suffix: str
    ) -> dict:
        return {
            "name": name,
            "unique_id": f"door_controller_{self.entity_prefix}_{unique_suffix}",
            "default_entity_id": entity_id,
            "availability_topic": self.availability_topic,
            "payload_available": "online",
            "payload_not_available": "offline",
            "device": self._mqtt_device(),
            "origin": self._mqtt_origin(),
        }

    def _sensor_payload(
        self,
        entity_id: str,
        name: str,
        unique_suffix: str,
        state_topic: str,
        icon: str,
        entity_category: Optional[str] = None,
    ) -> dict:
        payload = self._base_discovery_payload(entity_id, name, unique_suffix)
        payload.update({"state_topic": state_topic, "icon": icon})

        if entity_category:
            payload["entity_category"] = entity_category

        return payload

    def _button_payload(
        self,
        entity_id: str,
        name: str,
        unique_suffix: str,
        command_topic: str,
        icon: str,
    ) -> dict:
        payload = self._base_discovery_payload(entity_id, name, unique_suffix)
        payload.update(
            {
                "command_topic": command_topic,
                "payload_press": "PRESS",
                "retain": False,
                "icon": icon,
            }
        )
        return payload

    def _cover_payload(self) -> dict:
        payload = self._base_discovery_payload(
            self.cover_entity, self.friendly_name, "cover"
        )
        payload.update(
            {
                "command_topic": self.cover_command_topic,
                "payload_open": "OPEN",
                "payload_close": "CLOSE",
                "payload_stop": None,
                "device_class": self.cover_device_class,
                "retain": False,
                "optimistic": not self.has_sensors,
            }
        )

        if self.has_sensors:
            payload.update(
                {
                    "state_topic": self.cover_state_topic,
                    "state_open": "open",
                    "state_opening": "opening",
                    "state_closed": "closed",
                    "state_closing": "closing",
                    "state_stopped": "stopped",
                }
            )

        return payload

    def _mqtt_device(self) -> dict:
        return {
            "identifiers": [f"door_controller_{self.entity_prefix}"],
            "name": self.friendly_name,
            "manufacturer": "AppDaemon",
            "model": "DoorController",
        }

    def _mqtt_origin(self) -> dict:
        return {"name": "DoorController AppDaemon"}

    def _door_state_to_cover_state(self, state: str) -> str:
        if state in {"open", "closed"}:
            return state

        if state == "intermediate":
            if self.pending_target == "open":
                return "opening"
            if self.pending_target == "closed":
                return "closing"
            return "stopped"

        return "None"

    def _event_topic(self, data: Optional[dict], kwargs: Optional[dict]) -> Any:
        if data and "topic" in data:
            return data["topic"]
        if kwargs and "topic" in kwargs:
            return kwargs["topic"]
        return None

    def _normalize_payload(self, payload: Any) -> str:
        if isinstance(payload, bytes):
            payload = payload.decode("utf-8", errors="ignore")
        return str(payload or "").strip().upper()

    def _normalize_state(self, state: Any) -> str:
        """
        Normalize Home Assistant/AppDaemon state values for comparisons.
        """
        return str(state).strip().lower()

    def _normalize_topic(self, topic: Any) -> str:
        normalized_topic = str(topic).strip().strip("/")
        return normalized_topic or "door_controller"

    def _slugify(self, value: Any) -> str:
        """
        Convert a label into a stable entity-id suffix.
        """
        replaced_value = str(value)
        for source, replacement in POLISH_CHAR_REPLACEMENTS.items():
            replaced_value = replaced_value.replace(source, replacement)

        normalized_value = unicodedata.normalize("NFKD", replaced_value)
        ascii_value = normalized_value.encode("ascii", "ignore").decode("ascii")
        slug = re.sub(r"[^a-z0-9]+", "_", ascii_value.strip().lower()).strip("_")
        return slug or "door_controller"

    def _build_generated_entity_id(self, domain: str, suffix: str) -> str:
        """
        Build an app-managed entity id from the configured prefix.
        """
        return f"{domain}.{self.entity_prefix}_{suffix}"

    def _build_friendly_name(self, suffix: str) -> str:
        """
        Build a localized display name for an app-managed entity.
        """
        return f"{self.friendly_name}: {suffix}"

    def _discovery_topic(self, component: str, object_id: str) -> str:
        discovery_object_id = self._slugify(f"{self.entity_prefix}_{object_id}")
        return f"{self.mqtt_discovery_prefix}/{component}/{discovery_object_id}/config"
