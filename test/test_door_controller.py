import datetime

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


def test_initialize_creates_generated_entities_and_listeners(make_controller):
    controller = make_controller(
        {"door_relay": "switch.door_relay", "friendly_name": "Garage Door"}
    )

    assert controller.input_button_open == "input_button.garage_door_open"
    assert controller.input_button_close == "input_button.garage_door_close"
    assert controller.input_button_external == "input_button.garage_door_external"
    assert controller.door_status_sensor == "sensor.garage_door_status"
    assert controller.health_status_sensor == "sensor.garage_door_health"

    assert controller.state_store[controller.input_button_open]["state"] == "idle"
    assert controller.state_store[controller.input_button_close]["state"] == "idle"
    assert controller.state_store[controller.input_button_external]["state"] == "idle"
    assert controller.state_store[controller.door_status_sensor]["state"] == "unknown"
    assert controller.state_store[controller.health_status_sensor]["state"] == "healthy"

    listened_entities = {item["entity"] for item in controller.listeners}
    assert listened_entities == {
        controller.input_button_open,
        controller.input_button_close,
        controller.input_button_external,
    }


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

    controller.handle_open_event(controller.input_button_open, "state", "idle", "idle", {})

    assert controller.turn_on_calls == ["switch.door_relay"]
    assert [call["delay"] for call in controller.run_in_calls] == [0.5, 5]
    assert controller.pending_target == "open"

    controller.set_state("binary_sensor.closed", state="on")
    controller.door_status_changed(
        "binary_sensor.closed", "state", "off", "on", {}
    )

    assert controller.door_state == "intermediate"

    _set_timed_out(controller)
    controller.run_diagnostics({})

    assert controller.door_state == "open"
    assert controller.pending_target is None


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
        controller.input_button_close, "state", "idle", "idle", {}
    )
    controller.door_status_changed(
        "binary_sensor.closed", "state", "on", "on", {}
    )

    assert controller.door_state == "intermediate"
    assert controller.pending_target == "closed"

    _set_timed_out(controller)
    controller.run_diagnostics({})

    assert controller.door_state == "faulty"
    assert controller.pending_target is None


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
        controller.input_button_external, "state", "idle", "idle", {}
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
