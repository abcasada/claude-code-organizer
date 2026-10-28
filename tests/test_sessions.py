import json
import os

from conftest import assistant_line, title_line, user_line

from claude_code_organizer import paths
from claude_code_organizer.sessions import LiveSession, live_key, prompt_text

CWD = os.path.join(os.sep, "work", "my project") if os.name != "nt" else r"C:\work\my project"


def test_title_falls_back_through_sources(fx):
    sid, transcript = fx.write_session(CWD, [user_line("fix the login bug", CWD)])
    assert fx.session(sid).title == "fix the login bug"

    with open(transcript, "a", encoding="utf-8") as f:
        f.write(title_line("ai-title", "Login bug fix") + "\n")
    assert fx.session(sid).title == "Login bug fix"

    with open(transcript, "a", encoding="utf-8") as f:
        f.write(title_line("agent-name", "Agent name") + "\n")
        f.write(title_line("custom-title", "My rename") + "\n")
    assert fx.session(sid).title == "My rename"

    title_file = transcript.with_suffix("") / "custom-title.json"
    title_file.parent.mkdir()
    title_file.write_text(json.dumps({"customTitle": "From title file"}), encoding="utf-8")
    assert fx.session(sid).title == "From title file"


def test_first_prompt_skips_machine_generated_entries(fx):
    sid, _ = fx.write_session(CWD, [
        user_line("<local-command-caveat>Caveat</local-command-caveat>", CWD, isMeta=True),
        user_line("<command-name>/model</command-name><command-args>opus</command-args>", CWD),
        user_line("This session is being continued from a previous conversation that ran out", CWD),
        user_line([{"type": "tool_result", "content": "output"}], CWD),
        user_line("<system-reminder>context</system-reminder>\n\n  real   question  here", CWD),
    ])
    assert fx.session(sid).first_prompt == "real question here"


def test_prompt_text_handles_blocks_and_pasted_content():
    assert prompt_text([{"type": "image"}, {"type": "text", "text": "look at this"}]) == "look at this"
    assert prompt_text('<pasted_content id="1">pasted text</pasted_content>') == "pasted text"
    assert prompt_text("<command-message>x</command-message>") is None
    assert prompt_text("x" * 500).endswith("...")


def test_session_without_prompt_is_empty(fx):
    sid, _ = fx.write_session(CWD, [user_line("<command-name>/model</command-name>", CWD)])
    assert not fx.session(sid).has_content


def test_scripted_sessions_are_flagged(fx):
    sid, _ = fx.write_session(CWD, [user_line("summarize", CWD, entrypoint="sdk-cli")])
    assert fx.session(sid).is_scripted


def test_incremental_update_ignores_partial_last_line(fx):
    sid, transcript = fx.write_session(CWD, [user_line("first", CWD)])
    session = fx.session(sid)
    complete = transcript.stat().st_size
    assert session.offset == complete

    line = title_line("ai-title", "Finished title")
    with open(transcript, "a", encoding="utf-8") as f:
        f.write(line[:20])  # Claude is mid-write
    session.update()
    assert session.offset == complete
    assert session.ai_title is None

    with open(transcript, "a", encoding="utf-8") as f:
        f.write(line[20:] + "\n")
    session.update()
    assert session.ai_title == "Finished title"


def test_truncated_file_is_reparsed(fx):
    sid, transcript = fx.write_session(CWD, [user_line("old prompt", CWD), title_line("ai-title", "Old")])
    session = fx.session(sid)
    transcript.write_text(user_line("new", CWD) + "\n", encoding="utf-8")
    session.update()
    assert session.title == "new"


def test_cwd_matches_the_project_folder(fx):
    other = CWD + "-other"
    # History copied from another folder: the first cwd seen is not this project's.
    sid, _ = fx.write_session(CWD, [user_line("start", other), assistant_line("ok", CWD)])
    assert fx.session(sid).cwd == CWD


def test_long_paths_match_truncated_project_folder():
    long_path = CWD + os.sep + "x" * 250
    truncated = paths.encode_project_dir(long_path)[:200] + "-abc123"
    assert paths.matches_project_dir(long_path, truncated)


def test_store_lists_archived_sessions_and_prefers_active(fx):
    sid, transcript = fx.write_session(CWD, [user_line("hello", CWD)])
    archived_copy = fx.archive / "projects" / transcript.parent.name / transcript.name
    archived_copy.parent.mkdir(parents=True)
    archived_copy.write_bytes(transcript.read_bytes())

    session = fx.session(sid)
    assert not session.archived
    transcript.unlink()
    assert fx.session(sid).archived


def test_ignores_non_session_files(fx):
    fx.write_session(CWD, [user_line("x", CWD)], session_id="....")
    fx.store.refresh()
    assert fx.store.sessions == {}


def test_live_session_matching(fx):
    sid, _ = fx.write_session(CWD, [user_line("hi", CWD)])
    session = fx.session(sid)
    live = LiveSession(pid=1, session_id=sid, cwd=CWD + os.sep + "sub", status="idle", kind="interactive", job_id=None)
    fx.store.live = {live_key(sid, live.cwd): live}
    # Different cwd, but the id is unique, so it still matches.
    assert fx.store.live_session(session) is live
    assert live.label == "Idle"


def test_folder_key_normalizes_spellings():
    if os.name == "nt":
        assert paths.folder_key(r"\\?\C:\Work\Proj\\") == paths.folder_key("c:/work/proj")
    else:
        assert paths.folder_key("/work/proj/") == paths.folder_key("/work/proj")
