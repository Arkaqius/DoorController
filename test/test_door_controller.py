import datetime
import json

import pytest


def _set_timed_out(controller):
    controller.last_action_time = datetime.datetime.now() - datetime.timedelta(
        seconds=controller.timeout + 1
    )


def _dual_sensor_args():
    return {
        "door_relay": "switch.door_relay",
        "close_sensor": "binary_sensor.closed",
        "close_sensor_active_state": "on",
        "open_sensor": "binary_sensor.open",
        "open_sensor_active_state": "on",
        "timeout": 5,
    }


def _mqtt_service_calls(controller, service):
    return [call for call in controller.service_calls if call["service"] == service]


def _payloads_for_topic(controller, topic):
    payloads = []
    for call in _mqtt_service_calls(controller, "mqtt/publish"):
        if call["kwargs"]["topic"] != topic:
            continue

        payload = call["kwargs"]["payload"]
        if isinstance(payload, str) and payload.startswith("{"):
            payload = json.loads(payload)
        payloads.append(payload)

    return payloads


def _last_payload_for_topic(controller, topic):
    payloads = _payloads_for_topic(controller, topic)
    assert payloads
    return payloads[-1]


def test_initialize_publishes_mqtt_discovery_and_command_subscriptions(
    make_controller,
):
    controller = make_controller(
        {"door_relay": "switch.door_relay", "friendly_name": "Garage Door"}
    )

    assert controller.button_open_entity == "button.garage_door_open"
    assert controller.button_close_entity == "button.garage_door_close"
    assert controller.button_external_entity == "button.garage_door_external"
    assert controller.cover_entity == "cover.garage_door"
    assert controller.door_status_sensor == "sensor.garage_door_status"
    assert controller.health_status_sensor == "sensor.garage_door_health"
    assert controller.state_store == {}

    published_topics = {
        call["kwargs"]["topic"] for call in _mqtt_service_calls(controller, "mqtt/publish")
    }
    assert {
        "homeassistant/button/garage_door_open/config",
        "homeassistant/button/garage_door_close/config",
        "homeassistant/button/garage_door_external/config",
        "homeassistant/cover/garage_door_cover/config",
        "homeassistant/sensor/garage_door_status/config",
        "homeassistant/sensor/garage_door_health/config",
        "door_controller/garage_door/availability",
        "door_controller/garage_door/status/state",
        "door_controller/garage_door/health/state",
    } <= published_topics

    external_payload = _last_payload_for_topic(
        controller, "homeassistant/button/garage_door_external/config"
    )
    assert external_payload["default_entity_id"] == "button.garage_door_external"
    assert external_payload["command_topic"] == controller.button_external_command_topic
    assert external_payload["payload_press"] == "PRESS"

    cover_payload = _last_payload_for_topic(
        controller, "homeassistant/cover/garage_door_cover/config"
    )
    assert cover_payload["default_entity_id"] == "cover.garage_door"
    assert cover_payload["command_topic"] == controller.cover_command_topic
    assert cover_payload["payload_open"] == "OPEN"
    assert cover_payload["payload_close"] == "CLOSE"
    assert cover_payload["payload_stop"] is None
    assert cover_payload["optimistic"] is True
    assert "state_topic" not in cover_payload

    subscribed_topics = {
        call["kwargs"]["topic"] for call in _mqtt_service_calls(controller, "mqtt/subscribe")
    }
    assert subscribed_topics == {
        controller.cover_command_topic,
        controller.button_open_command_topic,
        controller.button_close_command_topic,
        controller.button_external_command_topic,
    }
    event_topics = {
        item["kwargs"]["topic"] for item in controller.event_listeners
    }
    assert event_topics == subscribed_topics


def test_mqtt_cover_discovery_uses_state_topic_when_sensors_are_configured(
    make_controller,
):
    controller = make_controller(
        {
            **_dual_sensor_args(),
            "friendly_name": "Garage Door",
            "entity_prefix": "garage_door",
            "cover_entity_id": "cover.brama_garazowa",
            "cover_device_class": "garage",
        },
        {"binary_sensor.closed": "on", "binary_sensor.open": "off"},
    )

    cover_payload = _last_payload_for_topic(
        controller, "homeassistant/cover/garage_door_cover/config"
    )
    assert cover_payload["default_entity_id"] == "cover.brama_garazowa"
    assert cover_payload["device_class"] == "garage"
    assert cover_payload["optimistic"] is False
    assert cover_payload["state_topic"] == controller.cover_state_topic
    assert _last_payload_for_topic(controller, controller.cover_state_topic) == "closed"


def test_polish_friendly_name_builds_readable_entity_prefix(make_controller):
    controller = make_controller(
        {"door_relay": "switch.door_relay", "friendly_name": "Brama garażowa"}
    )

    assert controller.button_open_entity == "button.brama_garazowa_open"
    assert controller.button_close_entity == "button.brama_garazowa_close"
    assert controller.button_external_entity == "button.brama_garazowa_external"
    assert controller.cover_entity == "cover.brama_garazowa"
    assert controller.door_status_sensor == "sensor.brama_garazowa_status"
    assert controller.health_status_sensor == "sensor.brama_garazowa_health"

    open_payload = _last_payload_for_topic(
        controller, "homeassistant/button/brama_garazowa_open/config"
    )
    assert open_payload["name"] == "Brama garażowa: Otwórz"


def test_entity_prefix_keeps_ids_stable_with_polish_friendly_name(make_controller):
    controller = make_controller(
        {
            "door_relay": "switch.door_relay",
            "friendly_name": "Brama garażowa",
            "entity_prefix": "garage_door",
            "cover_entity_id": "cover.brama_garazowa",
        }
    )

    assert controller.button_open_entity == "button.garage_door_open"
    assert controller.cover_entity == "cover.brama_garazowa"
    assert controller.door_status_sensor == "sensor.garage_door_status"
    assert _last_payload_for_topic(
        controller, "homeassistant/button/garage_door_open/config"
    )["name"] == "Brama garażowa: Otwórz"


def test_default_friendly_name_is_polish_without_changing_default_prefix(
    make_controller,
):
    controller = make_controller({"door_relay": "switch.door_relay"})

    assert controller.button_open_entity == "button.door_controller_open"
    assert _last_payload_for_topic(
        controller, "homeassistant/button/door_controller_open/config"
    )["name"] == "Sterownik drzwi: Otwórz"


def test_mqtt_cover_and_button_commands_trigger_relay(make_controller):
    controller = make_controller(
        _dual_sensor_args(),
        {"binary_sensor.closed": "on", "binary_sensor.open": "off"},
    )

    controller.handle_mqtt_command_event(
        "MQTT_MESSAGE",
        {"topic": controller.cover_command_topic, "payload": "OPEN"},
        {},
    )

    assert controller.turn_on_calls == ["switch.door_relay"]
    assert controller.pending_target == "open"

    controller.handle_mqtt_command_event(
        "MQTT_MESSAGE",
        {"topic": controller.button_external_command_topic, "payload": "PRESS"},
        {},
    )

    assert controller.turn_on_calls == ["switch.door_relay", "switch.door_relay"]
    assert controller.pending_target is None

    close_controller = make_controller(
        _dual_sensor_args(),
        {"binary_sensor.closed": "off", "binary_sensor.open": "on"},
    )

    close_controller.handle_mqtt_command_event(
        "MQTT_MESSAGE",
        {"topic": close_controller.button_close_command_topic, "payload": b"PRESS"},
        {},
    )

    assert close_controller.turn_on_calls == ["switch.door_relay"]
    assert close_controller.pending_target == "closed"


def test_single_close_sensor_open_becomes_intermediate_then_open(make_controller):
    controller = make_controller(
        {
            "door_relay": "switch.door_relay",
            "close_sensor": "binary_sensor.closed",
            "timeout": 5,
        },
        {"binary_sensor.closed": "off"},
    )

    assert controller.door_state == "closed"

    controller.handle_open_event(controller.button_open_entity, "command", None, "PRESS", {})

    assert controller.turn_on_calls == ["switch.door_relay"]
    assert [call["delay"] for call in controller.run_in_calls] == [0.5, 5]
    assert controller.pending_target == "open"

    controller.set_state("binary_sensor.closed", state="on")
    controller.door_status_changed(
        "binary_sensor.closed", "state", "off", "on", {}
    )

    assert controller.door_state == "intermediate"
    assert _last_payload_for_topic(controller, controller.cover_state_topic) == "opening"

    _set_timed_out(controller)
    controller.run_diagnostics({})

    assert controller.door_state == "open"
    assert controller.pending_target is None
    assert _last_payload_for_topic(controller, controller.cover_state_topic) == "open"


def test_single_close_sensor_close_timeout_sets_fault(make_controller):
    controller = make_controller(
        {
            "door_relay": "switch.door_relay",
            "close_sensor": "binary_sensor.closed",
            "timeout": 5,
        },
        {"binary_sensor.closed": "on"},
    )

    assert controller.door_state == "open"

    controller.handle_close_event(
        controller.button_close_entity, "command", None, "PRESS", {}
    )
    controller.door_status_changed(
        "binary_sensor.closed", "state", "on", "on", {}
    )

    assert controller.door_state == "intermediate"
    assert controller.pending_target == "closed"
    assert _last_payload_for_topic(controller, controller.cover_state_topic) == "closing"

    _set_timed_out(controller)
    controller.run_diagnostics({})

    assert controller.door_state == "faulty"
    assert controller.pending_target is None
    assert _last_payload_for_topic(controller, controller.cover_state_topic) == "None"


@pytest.mark.parametrize(
    ("target_state", "initial_states", "expected_start_state"),
    [
        (
            "open",
            {"binary_sensor.closed": "on", "binary_sensor.open": "off"},
            "closed",
        ),
        (
            "closed",
            {"binary_sensor.closed": "off", "binary_sensor.open": "on"},
            "open",
        ),
    ],
)
def test_dual_sensor_timeout_sets_fault_in_both_directions(
    make_controller, target_state, initial_states, expected_start_state
):
    controller = make_controller(_dual_sensor_args(), initial_states)

    assert controller.door_state == expected_start_state

    controller.request_target_state(target_state)

    controller.set_state("binary_sensor.closed", state="off")
    controller.set_state("binary_sensor.open", state="off")
    controller.door_status_changed("binary_sensor.closed", "state", "on", "off", {})

    assert controller.door_state == "intermediate"
    assert controller.pending_target == target_state

    _set_timed_out(controller)
    controller.run_diagnostics({})

    assert controller.door_state == "faulty"
    assert controller.pending_target is None


@pytest.mark.parametrize(
    ("target_state", "initial_states"),
    [
        ("open", {"binary_sensor.closed": "off", "binary_sensor.open": "on"}),
        ("closed", {"binary_sensor.closed": "on", "binary_sensor.open": "off"}),
    ],
)
def test_matching_target_request_is_ignored(
    make_controller, target_state, initial_states
):
    controller = make_controller(_dual_sensor_args(), initial_states)

    controller.request_target_state(target_state)

    assert controller.turn_on_calls == []
    assert controller.pending_target is None
    assert any("Ignoring" in entry["message"] for entry in controller.logs)


def test_external_button_pulses_without_tracking_target(make_controller):
    controller = make_controller(
        {
            "door_relay": "switch.door_relay",
            "close_sensor": "binary_sensor.closed",
            "timeout": 5,
        },
        {"binary_sensor.closed": "off"},
    )

    controller.request_target_state("open")
    original_call_count = len(controller.run_in_calls)
    original_diagnostic_handle = controller.diagnostic_handle

    controller.handle_external_button_event(
        controller.button_external_entity, "command", None, "PRESS", {}
    )

    assert controller.turn_on_calls == ["switch.door_relay", "switch.door_relay"]
    assert controller.pending_target is None
    assert controller.diagnostic_handle is None
    assert controller.cancelled_timers == [original_diagnostic_handle]

    new_calls = controller.run_in_calls[original_call_count:]
    assert [call["delay"] for call in new_calls] == [0.5]


def test_faulty_state_does_not_block_new_command(make_controller):
    controller = make_controller(
        _dual_sensor_args(),
        {"binary_sensor.closed": "on", "binary_sensor.open": "on"},
    )

    assert controller.door_state == "faulty"

    controller.request_target_state("open")

    assert controller.turn_on_calls == ["switch.door_relay"]
    assert controller.door_state == "faulty"
    assert controller.pending_target == "open"
