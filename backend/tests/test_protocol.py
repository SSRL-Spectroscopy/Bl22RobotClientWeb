import pytest

from robotweb.control.protocol import (
    ProtocolError,
    encode_request,
    parse_response,
    parse_status,
)


def test_encode_request_appends_crlf():
    assert encode_request("SAMPLE GET_STATUS") == b"SAMPLE GET_STATUS\r\n"


@pytest.mark.parametrize("bad", ["", "   ", "MOTOR MOVE X 1\r\nMOTOR MOVE Y 2"])
def test_encode_request_rejects_empty_and_multiline(bad):
    with pytest.raises(ValueError):
        encode_request(bad)


def test_success_without_data():
    response = parse_response(b"SUCCESS SAMPLE GET_FRAME \r\n\r\n")
    assert response.ok
    assert response.lines == ()


def test_success_with_one_data_line():
    response = parse_response(b"SUCCESS SCAN GET_MONO_ENERGY \r\n5966.002295\r\n\r\n")
    assert response.ok
    assert response.first == "5966.002295"


def test_success_with_several_data_lines():
    raw = (
        b"SUCCESS SAMPLE GET_SAMPLE_STORAGE_SLOTS \r\n"
        b"1 1 1 1 0 0 0 1 1 1 1 0 \r\n"
        b"1 1 1 1 1 1 0 1 1 1 1 0 \r\n"
        b"1 1 1 1 1 1 1 1 0 0 0 0\r\n\r\n"
    )
    response = parse_response(raw)
    assert len(response.lines) == 3
    assert response.lines[2] == "1 1 1 1 1 1 1 1 0 0 0 0"


def test_bytes_captured_from_the_live_server():
    status = parse_response(b"SUCCESS SAMPLE GET_STATUS \rIDLE NO_ERROR\r\n\r\n")
    assert status.lines == ("IDLE NO_ERROR",)

    slots = parse_response(
        b"SUCCESS SAMPLE GET_SAMPLE_STORAGE_SLOTS \r1 1 1 1 0 0 0 1 1 1 1 0 "
        b"\r1 1 1 1 1 1 0 1 1 1 1 0 \r1 1 1 1 1 1 1 1 0 0 0 0\r\n\r\n"
    )
    assert slots.lines[0] == "1 1 1 1 0 0 0 1 1 1 1 0"
    assert len(slots.lines) == 3

    error = parse_response(
        b"ERROR: OmServer.HandleClient Server EvaluateRequest "
        b"SampleSrv.EvaluateRequest UNKNOWN_COMMAND\r\n\r\n"
    )
    assert not error.ok and error.error.endswith("UNKNOWN_COMMAND")


def test_error_response():
    raw = b'ERROR: OmServer.HandleClient SchedulerSrv.GetFrameInfo HTTP_404 {"detail":"Unknown barcode"}\r\n'
    response = parse_response(raw)
    assert not response.ok
    assert "Unknown barcode" in response.error
    assert response.lines == ()


def test_garbage_is_a_protocol_error():
    with pytest.raises(ProtocolError):
        parse_response(b"HELLO\r\n")
    with pytest.raises(ProtocolError):
        parse_response(b"\r\n")


@pytest.mark.parametrize(
    ("line", "state", "idle", "failed"),
    [
        ("IDLE NO_ERROR", "IDLE", True, False),
        ("IDLE no_error", "IDLE", True, False),
        ("MoveSampleToBeam NO_ERROR", "MoveSampleToBeam", False, False),
        ("IDLE GRIPPER_FAULT", "IDLE", True, True),
        # the steps of filling the ion chambers have names of several words
        ("AutoGas Shutoff HV no_error", "AutoGas Shutoff HV", False, False),
        ("AutoGas FILL I0 no_error", "AutoGas FILL I0", False, False),
        ("AutoGas Restore HV no_error", "AutoGas Restore HV", False, False),
        ("AutoGas FILL I0 VALVE_FAULT", "AutoGas FILL I0", False, True),
        ("IDLE gas pressure low", "IDLE", True, True),
        ("IDLE", "IDLE", True, False),
        ("Homing", "Homing", False, False),
    ],
)
def test_parse_status(line, state, idle, failed):
    response = parse_response(f"SUCCESS SAMPLE GET_STATUS \r\n{line}\r\n\r\n".encode())
    status = parse_status(response)
    assert (status.state, status.idle, status.failed) == (state, idle, failed)


def test_parse_status_of_failed_request():
    with pytest.raises(ProtocolError):
        parse_status(parse_response(b"ERROR: something\r\n"))
