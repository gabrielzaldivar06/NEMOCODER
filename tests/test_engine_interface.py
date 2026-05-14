def _make_request(**kwargs):
    from nemo_coding_platform.core.engine_interface import MutationRequest
    defaults = dict(
        objective="Add a hello function",
        spec_path="generated-spec.md",
        acceptance_criteria=("hello() returns 'hello'",),
        context="NEMO context here",
    )
    defaults.update(kwargs)
    return MutationRequest(**defaults)


def test_render_engine_message_includes_repo_map():
    from nemo_coding_platform.core.engine_interface import render_engine_message
    req = _make_request(repo_map="## src/\n  foo.py — Foo module.\n")
    msg = render_engine_message(req)
    assert "# Repo Map" in msg
    assert "foo.py — Foo module." in msg


def test_render_engine_message_empty_repo_map_omitted():
    from nemo_coding_platform.core.engine_interface import render_engine_message
    req = _make_request(repo_map="")
    msg = render_engine_message(req)
    assert "# Repo Map" not in msg


def test_mutation_request_repo_map_defaults_to_empty():
    from nemo_coding_platform.core.engine_interface import MutationRequest
    req = MutationRequest(
        objective="x", spec_path="s", acceptance_criteria=(), context=""
    )
    assert req.repo_map == ""
