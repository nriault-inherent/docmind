import io
import wave

import bcrypt
import pytest
import yaml
from fastapi.testclient import TestClient

import app as web


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DOCMIND_PROJECTS_PATH", str(tmp_path / "projects"))
    users = {
        name: {
            "name": name,
            "password": bcrypt.hashpw(b"correct", bcrypt.gensalt(rounds=4)).decode(),
        }
        for name in ["nico", "other"]
    }
    path = tmp_path / "config.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "credentials": {"usernames": users},
                "cookie": {"name": "docmind", "expiry_days": 1},
            }
        )
    )
    with TestClient(web.create_app(path)) as value:
        yield value


def login(client, name="nico"):
    result = client.post("/api/login", json={"username": name, "password": "correct"})
    return {"X-CSRF-Token": result.json()["csrf_token"]}


def create(client, headers, name="Mon projet"):
    response = client.post("/api/projects", headers=headers, json={"name": name})
    assert response.status_code == 200
    return response.json()["id"]


def test_projects_persist_and_are_private(client, monkeypatch):
    headers = login(client)
    project = create(client, headers)
    assert client.get("/api/projects").json()["projects"][0]["name"] == "Mon projet"
    captured = []
    monkeypatch.setattr(
        web, "list_documents", lambda settings: captured.append(settings) or []
    )
    response = client.post(
        "/api/documents/list", headers=headers, json={"project_id": project}
    )
    assert response.status_code == 200
    assert captured[0].project_id == project
    assert captured[0].collection_name != web.default_settings().collection_name
    login(client, "other")
    assert client.get("/api/projects").json()["projects"] == []
    response = client.post(
        "/api/documents/list",
        headers=login(client, "other"),
        json={"project_id": project},
    )
    assert response.status_code == 404


def test_project_isolation_in_upload_delete_and_chat(client, monkeypatch):
    headers = login(client)
    first, second = create(client, headers, "A"), create(client, headers, "B")
    collections = {}

    def ingest(content, name, settings):
        collections.setdefault(settings.collection_name, []).append(name)
        return 1

    monkeypatch.setattr(web, "ingest_document", ingest)
    for project in (first, second):
        response = client.post(
            "/api/documents/upload",
            headers=headers,
            data={"settings": '{"project_id":"' + project + '"}'},
            files={"files": ("same.txt", b"notes")},
        )
        assert response.json()["results"][0]["success"]
    assert len(collections) == 2
    other_headers = login(client, "other")
    for path, payload in [
        (
            "/api/documents/delete",
            {"source": "same.txt", "settings": {"project_id": first}},
        ),
        ("/api/chat", {"question": "Résumé ?", "settings": {"project_id": first}}),
    ]:
        assert client.post(path, headers=other_headers, json=payload).status_code == 404


def wav_bytes():
    result = io.BytesIO()
    with wave.open(result, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(24000)
        audio.writeframes(b"\0\0" * 240)
    return result.getvalue()


def test_audio_generation_history_download_and_ownership(client, monkeypatch):
    headers = login(client)
    project = create(client, headers)
    from audio_summary import AudioSummary

    monkeypatch.setattr(
        web,
        "generate_audio_summary",
        lambda settings, detail, model: AudioSummary(
            "Les points essentiels.", wav_bytes(), ["notes.txt"]
        ),
    )
    response = client.post(
        f"/api/projects/{project}/audio",
        headers=headers,
        json={"detail": "brief", "settings": {}},
    )
    assert response.status_code == 200
    row = response.json()
    assert row["transcript"] == "Les points essentiels."
    assert row["detail"] == "brief"
    assert client.get(row["audio_url"]).content == wav_bytes()
    assert len(client.get(f"/api/projects/{project}/audio").json()["summaries"]) == 1
    login(client, "other")
    assert client.get(row["audio_url"]).status_code == 404
    assert client.get(f"/api/projects/{project}/audio").status_code == 404


def test_audio_requires_csrf_and_valid_detail(client):
    headers = login(client)
    project = create(client, headers)
    assert client.post(f"/api/projects/{project}/audio", json={}).status_code == 403
    assert (
        client.post(
            f"/api/projects/{project}/audio",
            headers=headers,
            json={"detail": "unknown"},
        ).status_code
        == 422
    )


def test_empty_project_does_not_call_models(monkeypatch):
    import audio_summary as audio
    from providers import ModelSettings, ProviderError

    class Collection:
        def get(self, **kwargs):
            return {"documents": [], "metadatas": []}

    monkeypatch.setattr(audio, "_collection", lambda settings: Collection())
    with pytest.raises(ProviderError, match="document"):
        audio.generate_audio_summary(ModelSettings(), "brief", "fish")


def test_summary_covers_all_fragments_and_combines_audio(monkeypatch):
    from types import SimpleNamespace

    import audio_summary as audio
    from providers import ModelSettings

    class Collection:
        def get(self, **kwargs):
            return {
                "documents": ["A" * 11000, "UNIQUE LAST DOCUMENT"],
                "metadatas": [{"source": "first.txt"}, {"source": "last.txt"}],
            }

    prompts = []

    class LLM:
        def complete(self, prompt):
            prompts.append(prompt)
            return SimpleNamespace(text="Une synthèse. " * 90)

    monkeypatch.setattr(audio, "_collection", lambda settings: Collection())
    monkeypatch.setattr(audio, "_models", lambda settings: (None, LLM()))
    monkeypatch.setattr(
        audio, "synthesize_speech", lambda settings, text, model: wav_bytes()
    )
    result = audio.generate_audio_summary(ModelSettings(), "detailed", "fish")
    assert any("UNIQUE LAST DOCUMENT" in prompt for prompt in prompts)
    assert result.sources == ["first.txt", "last.txt"]
    with wave.open(io.BytesIO(result.audio), "rb") as output:
        assert output.getnframes() > 240


def test_audio_models_are_not_offered_for_chat(monkeypatch):
    import providers

    monkeypatch.setattr(
        providers,
        "_json",
        lambda *args: {
            "data": [
                {"id": "chat"},
                {"id": "embed", "model_type": "embedding"},
                {"id": "fishaudio-s2-pro-8bit-mlx"},
                {"id": "voice", "model_type": "audio_tts"},
                {"id": "whisper", "model_type": "audio_stt"},
            ]
        },
    )
    catalog = providers.list_models(providers.ModelSettings())
    assert catalog.chat == ("chat",)
    assert catalog.tts == ("fishaudio-s2-pro-8bit-mlx", "voice")


def test_tts_uses_omlx_audio_endpoint_and_does_not_leak_errors(monkeypatch):
    import httpx

    import audio_summary as audio
    from providers import ModelSettings, ProviderError

    def handler(request):
        import json

        body = json.loads(request.content)
        assert request.url.path == "/v1/audio/speech"
        assert body["model"] == "fishaudio-s2-pro-8bit-mlx"
        assert body["response_format"] == "wav"
        assert body["language"] == "fr"
        return httpx.Response(200, content=wav_bytes())

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        monkeypatch.setattr(audio, "_client", lambda settings: client)
        assert (
            audio.synthesize_speech(
                ModelSettings(), "Bonjour.", "fishaudio-s2-pro-8bit-mlx"
            )
            == wav_bytes()
        )
    with httpx.Client(
        transport=httpx.MockTransport(
            lambda req: httpx.Response(500, text="private-token")
        )
    ) as client:
        monkeypatch.setattr(audio, "_client", lambda settings: client)
        with pytest.raises(ProviderError) as error:
            audio.synthesize_speech(ModelSettings(), "Bonjour.", "fish")
        assert "private-token" not in str(error.value)


def test_tts_explains_model_load_failure_without_exposing_weights(monkeypatch):
    import httpx

    import audio_summary as audio
    from providers import ModelSettings, ProviderError

    with httpx.Client(
        transport=httpx.MockTransport(
            lambda req: httpx.Response(
                500,
                json={
                    "error": {"message": "Missing 358 parameters: private-weight-name"}
                },
            )
        )
    ) as client:
        monkeypatch.setattr(audio, "_client", lambda settings: client)
        with pytest.raises(ProviderError, match="charger") as error:
            audio.synthesize_speech(ModelSettings(), "Bonjour.", "fish")
        assert "private-weight-name" not in str(error.value)


def test_invalid_or_inconsistent_wav_is_rejected():
    from audio_summary import join_wav
    from providers import ProviderError

    with pytest.raises(ProviderError, match="WAV"):
        join_wav([b'{"error":"Not audio"}'])
    other = io.BytesIO()
    with wave.open(other, "wb") as output:
        output.setnchannels(2)
        output.setsampwidth(2)
        output.setframerate(24000)
        output.writeframes(b"\0" * 960)
    with pytest.raises(ProviderError, match="WAV"):
        join_wav([wav_bytes(), other.getvalue()])


def test_failed_generation_does_not_create_history(client, monkeypatch):
    from providers import ProviderError

    headers = login(client)
    project = create(client, headers)

    def failed(*args):
        raise ProviderError("Impossible de charger le modèle vocal.")

    monkeypatch.setattr(web, "generate_audio_summary", failed)
    response = client.post(
        f"/api/projects/{project}/audio", headers=headers, json={"settings": {}}
    )
    assert response.status_code == 502
    assert client.get(f"/api/projects/{project}/audio").json()["summaries"] == []


def test_audio_history_survives_store_restart(tmp_path):
    from audio_summary import AudioSummary
    from projects import ProjectStore
    from providers import ModelSettings

    store = ProjectStore(tmp_path / "projects")
    project = store.create("nico", "Persistant")["id"]
    saved = store.save_summary(
        project,
        "brief",
        AudioSummary("Résumé conservé.", wav_bytes(), ["notes.txt"]),
        ModelSettings(project_id=project),
        "fish",
    )
    restarted = ProjectStore(tmp_path / "projects")
    assert restarted.owns("nico", project)
    assert restarted.summaries(project)[0]["transcript"] == "Résumé conservé."
    assert restarted.audio_path(project, saved["id"]).read_bytes() == wav_bytes()
