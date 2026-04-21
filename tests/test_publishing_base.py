import pytest

from edge_equation.publishing.base_publisher import PublishResult


def test_publish_result_construction_and_to_dict():
    r = PublishResult(success=True, target="x", message_id="x-1", error=None)
    d = r.to_dict()
    assert d == {
        "success": True,
        "target": "x",
        "message_id": "x-1",
        "error": None,
        "failsafe_triggered": False,
        "failsafe_detail": None,
    }


def test_publish_result_with_failsafe_fields():
    r = PublishResult(
        success=False, target="x", error="401",
        failsafe_triggered=True, failsafe_detail="file=/tmp/x-.txt",
    )
    d = r.to_dict()
    assert d["failsafe_triggered"] is True
    assert d["failsafe_detail"] == "file=/tmp/x-.txt"
    assert d["success"] is False


def test_publish_result_failure():
    r = PublishResult(success=False, target="discord", message_id=None, error="timeout")
    d = r.to_dict()
    assert d["success"] is False
    assert d["error"] == "timeout"
    assert d["message_id"] is None


def test_publish_result_is_frozen():
    r = PublishResult(success=True, target="x")
    with pytest.raises(Exception):
        r.success = False
