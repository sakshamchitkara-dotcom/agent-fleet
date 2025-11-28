from fleet.trajectory import Trajectory, read


def test_roundtrip_and_partial_line(tmp_path):
    t = Trajectory(tmp_path / "a" / "w.jsonl", "worker-1")
    t.log("tool_call", name="read_file", input={"path": "x"})
    t.log("finish", status="done")
    with (tmp_path / "a" / "w.jsonl").open("a") as f:
        f.write('{"ts": 1, "agent"')  # simulate a concurrent half-written line
    recs = read(tmp_path / "a" / "w.jsonl")
    assert [r["event"] for r in recs] == ["tool_call", "finish"]
    assert recs[0]["agent"] == "worker-1" and recs[0]["input"] == {"path": "x"}
