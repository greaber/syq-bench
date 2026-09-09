"""Transport claims must distinguish observation, configuration and missing data."""

import json

import pytest

from syq_bench import public
from syq_bench.publication import public_capture
from syq_bench.transport import data_path, observed_transports


def run():
    return {
        "source": "/source",
        "destination": "example.invalid:/destination",
        "spec": {"name": "sample", "tools": [{"name": "syq", "kind": "syq"}]},
        "cases": [{"tool": "syq", "workload": "large-file", "repeats": []}],
    }


@pytest.mark.parametrize(
    ("log", "expected"),
    [
        ("transport: encrypted TCP planned (reachability preflight passed)", []),
        ("control: connected via ssh", []),
        ("syq: private-host: data connection via tcp 192.0.2.1:47600", ["tcp"]),
        ("syq: private-host: data over ssh (TCP unavailable: private error)", ["ssh"]),
        (
            "syq: private-host: data connection via tcp 192.0.2.1:47600\n"
            "syq: private-host: data over ssh (TCP port stopped answering)",
            ["ssh", "tcp"],
        ),
    ],
)
def test_only_data_connection_diagnostics_establish_an_observation(log, expected):
    assert observed_transports({"stderr_tail": log}) == expected


@pytest.mark.parametrize("stored", ["tcp", ["private-host"], ["tcp", "private-host"], [{"host": "private-host"}], []])
def test_stored_transport_names_are_allowlisted(stored):
    assert observed_transports({"observed_transports": stored}) == []


def test_public_transport_evidence_is_safe_and_survives_republication():
    data = run()
    data["cases"][0]["repeats"] = [{"stderr_tail": "syq: private-host: data connection via tcp 192.0.2.1:47600"}]
    clean = public_capture(data, "sample")
    assert clean["cases"][0]["repeats"][0]["observed_transports"] == ["tcp"]
    assert "private-host" not in json.dumps(clean) and "192.0.2.1" not in json.dumps(clean)
    assert public_capture(clean, "sample")["cases"] == clean["cases"]


def test_missing_repeats_and_mixed_transports_are_never_presented_as_uniform_tcp():
    data = run()
    case = data["cases"][0]
    assert data_path(data, case, [{}])[0] == "Unrecorded"
    assert data_path(data, case, [{"observed_transports": ["tcp"]}])[0] == "TCP · observed"
    assert data_path(data, case, [{"observed_transports": ["tcp"]}, {}])[0] == "TCP · partial record"
    assert data_path(data, case, [{"observed_transports": ["tcp"]}, {"observed_transports": ["ssh"]}])[0] == (
        "SSH + TCP · observed"
    )
    assert data_path(data, case, [])[0] == "Unrecorded"


@pytest.mark.parametrize("flag", ["--no-tcp", "--syq-no-tcp"])
def test_ssh_only_configuration_is_labelled_requested_not_observed(flag):
    data = run()
    data["spec"]["tools"][0]["args"] = [flag]
    assert data_path(data, data["cases"][0], [{}])[0] == "SSH · requested"


def test_local_filesystem_nfs_and_remote_source_are_distinguished():
    data = run()
    data["destination"] = "/destination"
    case = data["cases"][0]
    assert data_path(data, case, [{}])[0] == "Local copy"
    data["filesystems"] = {"source": "ext2/ext3", "destination": "nfs"}
    assert data_path(data, case, [{}])[0] == "Local copy · NFS"
    data["source"] = "example.invalid:/source"
    assert data_path(data, case, [{}])[0] == "Unrecorded"
    clean = public_capture(data, "sample")
    assert data_path(clean, clean["cases"][0], [{}])[0] == "Unrecorded"
    del data["source"]
    assert data_path(data, case, [{}])[0] == "Unrecorded"


@pytest.mark.parametrize(
    ("case_fs", "scratch_fs", "destination_fs", "expected"),
    [
        ({"source_fs": "ext4"}, "nfs", "ext4", "Local copy"),
        ({"source_fs": "nfs"}, "ext4", "ext4", "Local copy · NFS"),
        ({"source_fs": "ext4"}, "nfs", "nfs", "Local copy · NFS"),
        ({}, "nfs", "ext4", "Local copy · NFS"),
        ({"source_fs": None}, "nfs", "ext4", "Local copy · NFS"),
        ({"source_fs": ""}, "nfs", "ext4", "Local copy · NFS"),
    ],
)
def test_nfs_label_uses_actual_source_filesystem_before_scratch_root(case_fs, scratch_fs, destination_fs, expected):
    data = run()
    data["destination"] = "/destination"
    data["filesystems"] = {"source": scratch_fs, "destination": destination_fs}
    case = data["cases"][0]
    case.update(source="/user-source", **case_fs)
    assert data_path(data, case, [{}])[0] == expected


def test_repeat_table_keeps_each_observation_separate():
    data = run()
    case = data["cases"][0]
    case["repeats"] = [{"index": 0, "observed_transports": ["tcp"]}, {"index": 1}]
    table = public.details_table([public.Result(data, case, "syq")])
    assert "TCP · observed" in table and "Unrecorded" in table
