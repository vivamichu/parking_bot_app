from parking_bot.config import ROOT, Settings, settings


def test_defaults_present():
    assert settings.chat_model
    assert settings.embedding_model
    assert settings.top_k >= 1


def test_relative_paths_resolved_against_root():
    # Paths in settings should be absolute (resolved against project root).
    assert settings.sqlite_path.endswith("parking_dynamic.db")
    assert str(ROOT) in settings.sqlite_path
    assert str(ROOT) in settings.milvus_uri


def test_require_openai_raises_when_missing():
    s = Settings(openai_api_key="")
    try:
        s.require_openai()
        assert False, "expected RuntimeError"
    except RuntimeError:
        pass
