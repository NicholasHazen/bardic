"""Voice library API: design, clone, iterate, assign and manage narration voices.

Voices belong to the whole library; drafts and saves may carry a book and
character as context. Provider calls never run while the store lock is held.
Breeze calls are free but occupy the owner's GPU; every Gemini voice create is
billed and requires ``confirm_cost``. Mutations that change which voice a
character follows are refused while narration jobs run for affected books.

Every request is validated before a provider voice is created, and a server
voice uploaded for a record that then cannot be written is removed again
(best effort), so a failed save or clone leaves no orphan for a later Breeze
check to import.
"""
from __future__ import annotations

import copy
import hashlib
from contextlib import nullcontext
from typing import Literal
from uuid import uuid4

from fastapi import File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, ConfigDict, Field

from . import breeze, gemini_voices
from .audio import BREEZE_MODEL, AudioError, UncertainRequest, _VOICE_NAMES, list_system_voices
from .audio_refs import audio_ref
from .errors import ApiError, Conflict, Invalid, NotFound, ProviderFailure, TooLarge
from .store import now
from .voice_library import MAX_DESCRIPTION, MAX_NAME
from .voice_previews import DEMO_TEXT, _excerpt

ACTIVE = {"queued", "running"}
NARRATION_JOBS = {"render", "listen", "listen_chapter", "voice_preview"}
GEMINI_VOICE_LIMIT = 200


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DraftCreate(StrictModel):
    provider: Literal["breeze", "gemini"]
    base_voice_id: str | None = Field(default=None, max_length=40)
    book_id: str | None = Field(default=None, max_length=200)
    character_id: str | None = Field(default=None, max_length=200)
    name: str | None = Field(default=None, max_length=100)
    description: str | None = Field(default=None, max_length=1000)
    sample_text: str | None = Field(default=None, max_length=1000)


class DraftEdit(StrictModel):
    name: str | None = Field(default=None, max_length=100)
    description: str | None = Field(default=None, max_length=1000)
    sample_text: str | None = Field(default=None, max_length=1000)


class GenerateRequest(StrictModel):
    book_id: str | None = Field(default=None, max_length=200)
    count: int = Field(default=2, ge=1, le=breeze.MAX_PREVIEWS)
    language_code: str = Field(default="en-US", max_length=20)
    gender: Literal["female", "male", "neutral"] | None = None
    confirm_cost: bool = False


class Assignment(StrictModel):
    book_id: str = Field(max_length=200)
    character_id: str = Field(max_length=200)


class SaveRequest(StrictModel):
    candidate_id: str = Field(max_length=20)
    name: str = Field(min_length=1, max_length=100)
    mode: Literal["new", "version"] = "new"
    assign: Assignment | None = None
    make_default: bool = False


class VoiceEdit(StrictModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    description: str | None = Field(default=None, max_length=1000)


class CurrentVersion(StrictModel):
    version: int = Field(ge=1)


class DefaultVoice(StrictModel):
    provider: Literal["breeze"]
    voice_id: str = Field(max_length=40)


def key_hash(key: str | None) -> str | None:
    """Identify the Google project key without storing it."""
    return hashlib.sha256(key.strip().encode()).hexdigest()[:12] if key and key.strip() else None


def register(app, rt, edit):
    """Add the voice routes. ``edit`` is the app's cast edit function (it enforces require_idle)."""

    # Helpers ---------------------------------------------------------------

    def require_no_narration(runtime, book_ids=None):
        active = [job for job in runtime.store.jobs(limit=None)
                  if job["status"] in ACTIVE and job["kind"] in NARRATION_JOBS
                  and (book_ids is None or job["book_id"] in book_ids)]
        if active:
            raise Conflict("narration_active", "Narration is being prepared for a book that uses this voice.")

    def followers(runtime, voice_id):
        return runtime.voices.usage(runtime.narration_defaults()).get(voice_id, [])

    def gemini_catalog(runtime):
        catalog = runtime.preferences.get("gemini_voice_catalog") or {}
        current = catalog.get("key_hash") == key_hash(runtime.api_keys["gemini"]) and catalog.get("key_hash")
        return catalog if current else {}

    def provider_checks(runtime):
        """The saved Breeze check and Gemini listing, read locally; never contacts a provider.

        Returns ``(breeze_view, breeze_voices, gemini)``: ``breeze_voices`` maps
        server voice IDs of the last check for the current URL (None when never
        checked), and ``gemini`` is the last listing for the current key ({} when none).
        """
        with runtime.store.lock:
            breeze_view = runtime.breeze_view()
            gemini = copy.deepcopy(gemini_catalog(runtime))
        breeze_voices = ({voice["id"]: voice for voice in breeze_view["voices"]}
                         if breeze_view["checked_at"] else None)
        return breeze_view, breeze_voices, gemini

    def version_state(runtime, voice, version, breeze_voices, gemini):
        if version["provider_voice_id"] in (voice.get("server_deleted") or ()):
            return "missing"  # An unfinished deletion already removed it.
        if voice["provider"] == "breeze":
            if breeze_voices is None:
                return "unknown"
            server = breeze_voices.get(version["provider_voice_id"])
            if server is None:
                return "missing"
            return "ok" if server.get("revision") == version["revision"] else "changed"
        if version.get("project") and version["project"] != key_hash(runtime.api_keys["gemini"]):
            return "other_project"
        if not gemini or gemini.get("voices") is None:
            return "unknown"  # Never listed with this key, or the last listing failed.
        return "ok" if version["provider_voice_id"] in {v["id"] for v in gemini["voices"]} else "missing"

    def audition_ref(voice, version):
        retained = version.get("audition") or {}
        # A designed version's audition was generated by the provider's design model; a cloned or
        # imported one is a recording, so no model produced it.
        model = ((version.get("recipe") or {}).get("model") if voice["provider"] == "gemini"
                 else BREEZE_MODEL if version.get("made") == "designed" else None)
        return audio_ref(f"/api/voices/{voice['id']}/versions/{version['version']}/audition",
                         asset_id=retained.get("asset_id"), duration=retained.get("duration"),
                         provider=voice["provider"], model=model, voice=version["provider_voice_id"],
                         created_at=version["created_at"] if retained.get("asset_id") else None)

    def voice_view(runtime, voice, *, usage=None, checks=None, defaults=None):
        """``checks`` is ``provider_checks(runtime)``; read here when not given."""
        defaults = runtime.narration_defaults() if defaults is None else defaults
        _, breeze_voices, gemini = checks if checks is not None else provider_checks(runtime)
        versions, warnings = [], []
        for version in voice["versions"]:
            state = version_state(runtime, voice, version, breeze_voices, gemini)
            recipe = version.get("recipe") or {}
            versions.append({"version": version["version"], "provider_voice_id": version["provider_voice_id"],
                             "revision": version.get("revision"),
                             "made": version["made"], "created_at": version["created_at"],
                             "expires_at": version.get("expires_at"), "server_state": state,
                             "audition": audition_ref(voice, version),
                             "recipe": {key: recipe.get(key) for key in ("description", "sample_text", "model", "language_code", "gender")
                                        if recipe.get(key) is not None}})
        current = next(v for v in versions if v["version"] == voice["current_version"])
        if voice.get("server_deleted") and not voice.get("deleted_at"):
            warnings.append(f"An unfinished deletion already removed {len(voice['server_deleted'])} of this voice's "
                            "provider voices. Delete the voice again to finish.")
        if current["server_state"] == "changed":
            warnings.append("Changed on the Breeze server since it was saved; narration with it is refused until you choose another voice or version.")
        elif current["server_state"] == "missing":
            warnings.append("The current version is no longer on the server.")
        elif current["server_state"] == "other_project":
            warnings.append("Made with a different Google API key, so this project cannot use it.")
        designed_ok = voice["provider"] != "gemini" or runtime.preferences["tts_model"] in gemini_voices.DESIGN_MODELS
        if not designed_ok:
            warnings.append("The selected Gemini speech model accepts only built-in voices, so this voice cannot narrate until a 3.8 speech model is selected.")
        return {"id": voice["id"], "provider": voice["provider"], "name": voice["name"], "description": voice["description"],
                "origin": voice["origin"], "current_version": voice["current_version"],
                "is_default": defaults.get(voice["provider"]) == voice["id"], "deleted": bool(voice.get("deleted_at")),
                "assignable": current["server_state"] in ("ok", "unknown") and designed_ok,
                "versions": versions, "usage": usage if usage is not None else followers(runtime, voice["id"]),
                "source": voice.get("source"), "warnings": warnings}

    def candidate_audio_ref(draft, candidate):
        if not candidate.get("asset_id"):
            return None
        model = candidate.get("model") if draft["provider"] == "gemini" else BREEZE_MODEL
        return audio_ref(f"/api/voices/drafts/{draft['id']}/candidates/{candidate['id']}/audio",
                         asset_id=candidate["asset_id"], duration=candidate.get("duration"), provider=draft["provider"],
                         model=model, voice=candidate.get("provider_voice_id"), created_at=candidate.get("created_at"))

    def draft_view(runtime, draft):
        base_name = None
        if draft.get("base_voice_id"):
            try:
                base_name = runtime.voices.voice(draft["base_voice_id"])["name"]
            except KeyError:
                base_name = None
        return {**{key: draft[key] for key in ("id", "provider", "base_voice_id", "context", "name", "description",
                                                 "sample_text", "status", "created_at", "updated_at")},
                "base_voice_name": base_name, "busy": draft["id"] in runtime.voice_busy,
                "candidates": [{**{key: candidate.get(key) for key in (
                    "id", "kind", "seed", "provider_voice_id", "description", "sample_text", "duration",
                    "created_at", "expires_at", "discarded")},
                    "audio": candidate_audio_ref(draft, candidate)} for candidate in draft["candidates"]]}

    def library_view(runtime):
        checks = provider_checks(runtime)
        breeze_view, _, gemini = checks
        with runtime.store.lock:
            defaults = runtime.narration_defaults()
            gemini_state = {"has_api_key": bool(runtime.api_keys["gemini"]), "tts_model": runtime.preferences["tts_model"]}
        usage = runtime.voices.usage(defaults)
        voices = [voice_view(runtime, voice, usage=usage.get(voice["id"], []), checks=checks, defaults=defaults)
                  for voice in runtime.voices.voices()]
        in_library = {version["provider_voice_id"] for voice in runtime.voices.voices(include_deleted=True)
                      if voice["provider"] == "gemini" for version in voice["versions"]}
        drafts = runtime.voices.drafts()
        draft_voices = {candidate.get("provider_voice_id") for draft in drafts for candidate in draft["candidates"]
                        if candidate.get("kind") == "gemini_voice" and not candidate.get("discarded")}
        listed = gemini.get("voices")  # None when never listed with this key or the last listing failed
        return {
            "voices": voices, "defaults": defaults, "drafts": [draft_view(runtime, draft) for draft in drafts],
            "providers": {
                "breeze": breeze_view,
                "gemini": {**gemini_state, "state": gemini.get("state", "unchecked"), "message": gemini.get("message", ""),
                           "checked_at": gemini.get("checked_at"), "design_models": list(gemini_voices.DESIGN_MODELS),
                           "designed_voices_supported": gemini_state["tts_model"] in gemini_voices.DESIGN_MODELS,
                           "stored_count": len(listed) if listed is not None else None,
                           "limit": GEMINI_VOICE_LIMIT,
                           "project_voices": [{**{key: voice.get(key) for key in ("id", "display_name", "type", "description", "language_code")},
                                               "in_library": voice["id"] in in_library, "draft_candidate": voice["id"] in draft_voices}
                                              for voice in listed or []]},
            },
            "builtin": {"gemini": list(_VOICE_NAMES), "system": list_system_voices()},
        }

    def set_default(runtime, provider, voice_id):
        with runtime.store.lock:
            preferences = copy.deepcopy(runtime.preferences)
            previous = (preferences.get("narration_defaults") or {}).get(provider)
            preferences.setdefault("narration_defaults", {})[provider] = voice_id
            runtime.store.save_settings(preferences)
            runtime.preferences = preferences
        if previous != voice_id:
            runtime.voices.record("default_changed", provider=provider, voice_id=voice_id, previous=previous)

    def note_breeze_voices(runtime, *, added=None, removed=()):
        """Keep the saved Breeze check in step with voices Bardic itself creates or deletes."""
        with runtime.store.lock:
            catalog = runtime.preferences.get("breeze_catalog")
            if not catalog or catalog.get("base_url") != runtime.breeze_url():
                return
            preferences = copy.deepcopy(runtime.preferences)
            voices = [voice for voice in preferences["breeze_catalog"].get("voices", [])
                      if voice["id"] not in set(removed) and (not added or voice["id"] != added["id"])]
            if added:
                voices.append({"id": added["id"], "name": added.get("name") or added["id"], "kind": "cloned",
                               "description": added.get("description") or "", "labels": added.get("labels") or {},
                               "usable": True, "reason": None, "revision": added["revision"], "seed": None})
            preferences["breeze_catalog"]["voices"] = voices
            runtime.store.save_settings(preferences)
            runtime.preferences = preferences

    def undo_upload(config, pinned):
        """Remove a server voice uploaded for a library record that was then not written (best effort).

        Returns a sentence for the error detail saying what happened to it.
        """
        try:
            breeze.delete_voice(config, pinned["id"])
        except AudioError:
            return f" The uploaded Breeze server voice {pinned['id']} could not be removed and remains on the server."
        return " The uploaded Breeze server voice was removed."

    def require_book(runtime, book_id):
        try:
            return runtime.store.book(book_id)
        except KeyError:
            raise Invalid("unknown_book", "No book has the given book_id.") from None

    def ledger(runtime, book_id, provider, model):
        """Record voice-design requests against the book they were made from."""
        from .resources import ResourceLedger
        if not book_id:
            return nullcontext({})
        require_book(runtime, book_id)
        return ResourceLedger(runtime.store).operation(book_id, "voice_design", provider=provider, model=model,
                                                       kind="narration")

    def claim(runtime, draft_id, *, open_only=True):
        with runtime.store.lock:
            draft = runtime.voices.draft(draft_id)
            if open_only and draft["status"] != "open":
                raise Conflict("draft_finished", "The voice draft is already finished.")
            if draft_id in runtime.voice_busy:
                raise Conflict("draft_busy", "Another request is working on this voice draft.")
            runtime.voice_busy.add(draft_id)
        return draft

    def breeze_config(runtime):
        with runtime.store.lock:
            if not runtime.breeze_url():
                raise Invalid("breeze_url_missing", "No Breeze server URL is configured.")
            return runtime.breeze_config()

    def gemini_key(runtime):
        with runtime.store.lock:
            key = runtime.api_keys["gemini"]
        if not key:
            raise Invalid("gemini_key_missing", "No Gemini API key is configured.")
        return key

    def character_context(runtime, book_id, character_id):
        book = require_book(runtime, book_id)
        character = next((c for c in book["characters"] if c["id"] == character_id), None)
        if character is None:
            raise Invalid("unknown_character", "The book has no character with the given character_id.")
        line = next((s["text"] for s in book["segments"] if s.get("speaker_id") == character_id and s.get("text", "").strip()), None)
        return book, character, _excerpt(line) if line else DEMO_TEXT

    def assign(runtime, assignment, provider, voice_id):
        try:
            return edit(runtime, assignment.book_id, "characters", assignment.character_id,
                        {"voices": {provider: {"library": voice_id}}}), None
        except ApiError as error:
            return None, error.detail
        except HTTPException as error:
            return None, str(error.detail)
        except (KeyError, ValueError) as error:
            return None, str(error)

    def delete_quietly(key, voice_ids):
        failures = []
        for voice_id in voice_ids:
            try:
                gemini_voices.delete_voice(key, voice_id)
            except AudioError as error:
                failures.append(str(error))
        return failures

    # Library ---------------------------------------------------------------

    @app.get("/api/voices")
    def list_voices(request: Request):
        return library_view(rt(request))

    @app.post("/api/voices/gemini/refresh")
    def refresh_gemini(request: Request):
        runtime = rt(request)
        key = gemini_key(runtime)
        failure = None
        try:
            voices = gemini_voices.list_voices(key)
            catalog = {"state": "ready", "message": f"{len(voices)} stored voices in this Google project.", "voices": voices}
        except AudioError as error:
            failure = str(error)
            catalog = {"state": "error", "message": failure, "voices": None}
        with runtime.store.lock:
            if key != runtime.api_keys["gemini"]:
                raise Conflict("gemini_key_changed", "The Gemini API key changed during the check.")
            preferences = copy.deepcopy(runtime.preferences)
            preferences["gemini_voice_catalog"] = {**catalog, "key_hash": key_hash(key), "checked_at": now()}
            runtime.store.save_settings(preferences)
            runtime.preferences = preferences
        if failure:
            # Saved first, so GET /api/voices reports the failed check.
            raise ProviderFailure("provider_error", failure)
        return library_view(runtime)["providers"]["gemini"]

    @app.patch("/api/voices/{voice_id}")
    def edit_voice(voice_id: str, body: VoiceEdit, request: Request):
        runtime = rt(request)
        voice = runtime.voices.update(voice_id, name=body.name, description=body.description)
        if voice["provider"] == "breeze" and runtime.breeze_url():
            # Names and descriptions do not change the pinned revision.
            config = breeze_config(runtime)
            for provider_voice_id in {version["provider_voice_id"] for version in voice["versions"]}:
                try:
                    breeze.update_voice(config, provider_voice_id, name=body.name, description=body.description)
                except AudioError:
                    pass  # The library record is authoritative for Bardic's names.
        return voice_view(runtime, voice)

    @app.post("/api/voices/{voice_id}/current")
    def switch_version(voice_id: str, body: CurrentVersion, request: Request):
        runtime = rt(request)
        runtime.voices.voice(voice_id)
        require_no_narration(runtime, {row["book_id"] for row in followers(runtime, voice_id)})
        return voice_view(runtime, runtime.voices.set_current(voice_id, body.version))

    @app.delete("/api/voices/{voice_id}")
    def delete_voice(voice_id: str, request: Request, server: bool | None = None):
        runtime = rt(request)
        voice = runtime.voices.voice(voice_id)
        if voice.get("deleted_at"):
            return {"deleted": voice_id, "server_deleted": []}
        if runtime.narration_defaults().get(voice["provider"]) == voice_id:
            raise Conflict("voice_is_default", "The voice is the Breeze default voice.")
        require_no_narration(runtime, {row["book_id"] for row in followers(runtime, voice_id)})
        # Voices Bardic made are deleted on the provider by default; imported
        # server voices are only removed from Bardic unless asked explicitly.
        on_server = server if server is not None else voice["origin"] != "imported"
        provider_ids = sorted({version["provider_voice_id"] for version in voice["versions"]})
        done = [provider_voice_id for provider_voice_id in voice.get("server_deleted") or []]
        pending = [provider_voice_id for provider_voice_id in provider_ids if provider_voice_id not in done]
        if on_server and pending:
            if voice["provider"] == "breeze":
                config = breeze_config(runtime)
                remove = lambda provider_voice_id: breeze.delete_voice(config, provider_voice_id)  # noqa: E731
            else:
                key = gemini_key(runtime)
                if any(version.get("project") not in (None, key_hash(key)) for version in voice["versions"]
                       if version["provider_voice_id"] in pending):
                    raise Conflict("voice_other_project", "The voice has versions made with a different Google API key.")
                remove = lambda provider_voice_id: gemini_voices.delete_voice(key, provider_voice_id)  # noqa: E731
            removed = []
            try:
                for provider_voice_id in pending:
                    remove(provider_voice_id)
                    # Recorded one at a time: a failure part-way leaves a visible, resumable state.
                    runtime.voices.note_server_deleted(voice_id, provider_voice_id)
                    removed.append(provider_voice_id)
                    done.append(provider_voice_id)
            except AudioError as error:
                raise ProviderFailure(
                    "provider_error", f"{error} {len(done)} of {len(provider_ids)} provider voices are deleted; the "
                                      "voice stays in the library, and deleting it again resumes.") from None
            finally:
                if removed and voice["provider"] == "breeze":
                    note_breeze_voices(runtime, removed=removed)
        runtime.voices.tombstone(voice_id, server_deleted=sorted(done))
        return {"deleted": voice_id, "server_deleted": sorted(done)}

    @app.post("/api/voices/defaults")
    def choose_default(body: DefaultVoice, request: Request):
        runtime = rt(request)
        try:
            voice = runtime.voices.voice(body.voice_id)
        except KeyError:
            raise Invalid("unknown_voice", "No library voice has the given voice_id.") from None
        if voice.get("deleted_at") or voice["provider"] != body.provider:
            raise Invalid("default_voice_invalid", "Only a live Breeze library voice can be the Breeze default.")
        previous = runtime.narration_defaults().get(body.provider)
        affected = {row["book_id"] for row in followers(runtime, previous)} if previous else set()
        require_no_narration(runtime, affected | {row["book_id"] for row in followers(runtime, body.voice_id)})
        set_default(runtime, body.provider, body.voice_id)
        return {"defaults": runtime.narration_defaults()}

    @app.get("/api/voices/{voice_id}/versions/{version}/audition")
    def audition(voice_id: str, version: int, request: Request):
        runtime = rt(request)
        voice = runtime.voices.voice(voice_id)
        entry = next((item for item in voice["versions"] if item["version"] == version), None)
        if entry is None:
            raise NotFound("voice_version_not_found", "The voice has no version with that number.")
        if entry.get("audition"):
            path = runtime.voices.asset_path(entry["audition"]["asset_id"])
            if path.is_file():
                return FileResponse(path, media_type="audio/wav")
        try:
            if voice["provider"] == "breeze":
                data = breeze.reference_audio(breeze_config(runtime), entry["provider_voice_id"])
            else:
                data = gemini_voices.voice_sample(gemini_key(runtime), entry["provider_voice_id"])
        except AudioError as error:
            raise ProviderFailure("provider_error", str(error)) from None
        if not data:
            raise NotFound("audio_not_found", "No audition audio is available for this voice version.")
        return Response(content=data, media_type="audio/wav")

    # Drafts ----------------------------------------------------------------

    @app.post("/api/voices/drafts")
    def create_draft(body: DraftCreate, request: Request):
        runtime = rt(request)
        name, description, sample_text, context = body.name, body.description, body.sample_text, None
        if body.book_id and body.character_id:
            book, character, line = character_context(runtime, body.book_id, body.character_id)
            context = {"book_id": book["id"], "character_id": character["id"], "character_name": character.get("name")}
            profile = " ".join(part.strip() for part in (character.get("description") or "", character.get("direction") or "")
                               if part and part.strip())
            name = name if name is not None else character.get("name", "")
            description = description if description is not None else profile[:1000]
            sample_text = sample_text if sample_text is not None else line
        if body.base_voice_id:
            try:
                base = runtime.voices.voice(body.base_voice_id)
            except KeyError:
                raise Invalid("unknown_voice", "No library voice has the given base_voice_id.") from None
            recipe = next(v for v in base["versions"] if v["version"] == base["current_version"]).get("recipe") or {}
            name = name if name is not None else base["name"]
            description = description if description is not None else (recipe.get("description") or base["description"])
            sample_text = sample_text if sample_text is not None else recipe.get("sample_text")
        draft = runtime.voices.create_draft(body.provider, name=name or "", description=description or "",
                                            sample_text=sample_text or (DEMO_TEXT if body.provider == "breeze" else ""),
                                            base_voice_id=body.base_voice_id, context=context)
        return draft_view(runtime, draft)

    @app.patch("/api/voices/drafts/{draft_id}")
    def edit_draft(draft_id: str, body: DraftEdit, request: Request):
        runtime = rt(request)
        draft = runtime.voices.edit_draft(draft_id, name=body.name, description=body.description, sample_text=body.sample_text)
        return draft_view(runtime, draft)

    @app.post("/api/voices/drafts/{draft_id}/generate")
    def generate(draft_id: str, body: GenerateRequest, request: Request):
        runtime = rt(request)
        draft = runtime.voices.draft(draft_id)
        if len(draft["description"].strip()) < 3:
            raise Invalid("description_too_short", "The voice description needs at least 3 characters.")
        if draft["provider"] == "gemini":
            if not body.confirm_cost:
                raise Invalid("cost_not_confirmed", "A Gemini voice design creates a billed, stored voice; "
                                                    "confirm_cost must be true.")
            if not body.book_id:
                raise Invalid("book_id_required", "A Gemini voice design needs a book_id, so that the paid request "
                                                  "is recorded in that book's resource ledger.")
            key = gemini_key(runtime)
            try:
                gemini_voices.validate_design(gemini_voices.DESIGN_MODELS[0], draft["name"] or "Bardic voice",
                                              draft["description"], body.language_code, body.gender)
            except AudioError as error:
                raise Invalid("voice_design_invalid", str(error)) from None
        else:
            if not draft["sample_text"].strip():
                raise Invalid("sample_text_missing", "The draft has no sample text for the previews to speak.")
            config = breeze_config(runtime)
        if body.book_id:
            require_book(runtime, body.book_id)
        draft = claim(runtime, draft_id)
        try:
            if draft["provider"] == "breeze":
                with ledger(runtime, body.book_id, "breeze", BREEZE_MODEL) as metrics:
                    previews = breeze.design_previews(config, draft["description"], draft["sample_text"], body.count)
                    candidates = []
                    for preview in previews:
                        stored = runtime.voices.store_audio(breeze.preview_audio(config, preview["id"]))
                        candidates.append({"kind": "breeze_preview", "preview_id": preview["id"], "seed": preview["seed"],
                                           "provider_voice_id": None, "description": draft["description"],
                                           "sample_text": draft["sample_text"], "asset_id": stored["asset_id"],
                                           "duration": stored["duration"], "expires_at": preview["expires_at"]})
                    metrics.update(request_count=1 + len(previews), estimated_cost_usd=0., cost_basis="self_hosted",
                                   audio_seconds=round(sum(c["duration"] for c in candidates), 3))
            else:
                model = (runtime.preferences["tts_model"] if runtime.preferences["tts_model"] in gemini_voices.DESIGN_MODELS
                         else gemini_voices.DESIGN_MODELS[0])
                with ledger(runtime, body.book_id, "gemini", model) as metrics:
                    # Unknown cost is not zero: this billed attempt stays unpriced.
                    metrics.update(request_count=1, estimated_cost_usd=None, cost_basis="unknown")
                    created = gemini_voices.create_voice(key, model=model, display_name=draft["name"] or "Bardic voice",
                                                         description=draft["description"], language_code=body.language_code,
                                                         gender=body.gender)
                    # The voice now exists (and is billed); a local sample failure must not lose its record.
                    try:
                        stored = runtime.voices.store_audio(created["sample"]) if created.get("sample") else None
                    except (AudioError, ValueError, OSError):
                        stored = None
                    candidates = [{"kind": "gemini_voice", "provider_voice_id": created["id"], "seed": None,
                                   "preview_id": None, "description": draft["description"], "sample_text": None,
                                   "asset_id": stored["asset_id"] if stored else None,
                                   "duration": stored["duration"] if stored else None,
                                   "expires_at": created.get("expire_time"), "project": key_hash(key), "model": model,
                                   "language_code": body.language_code, "gender": body.gender}]
                    if stored:
                        metrics["audio_seconds"] = stored["duration"]
            draft = runtime.voices.add_candidates(draft_id, candidates)
        except UncertainRequest as error:
            raise ProviderFailure("provider_outcome_unknown", str(error)) from None
        except AudioError as error:
            raise ProviderFailure("provider_error", str(error)) from None
        finally:
            with runtime.store.lock:
                runtime.voice_busy.discard(draft_id)
        return draft_view(runtime, draft)

    @app.get("/api/voices/drafts/{draft_id}/candidates/{candidate_id}/audio")
    def candidate_audio(draft_id: str, candidate_id: str, request: Request):
        runtime = rt(request)
        draft = runtime.voices.draft(draft_id)
        candidate = next((c for c in draft["candidates"] if c["id"] == candidate_id), None)
        if candidate is None:
            raise NotFound("candidate_not_found", "The draft has no candidate with that ID.")
        path = runtime.voices.asset_path(candidate["asset_id"]) if candidate.get("asset_id") else None
        if path is None or not path.is_file():
            raise NotFound("audio_not_found", "The candidate has no retained audio.")
        return FileResponse(path, media_type="audio/wav")

    @app.post("/api/voices/drafts/{draft_id}/candidates/{candidate_id}/discard")
    def discard(draft_id: str, candidate_id: str, request: Request):
        runtime = rt(request)
        draft = claim(runtime, draft_id)
        try:
            candidate = next((c for c in draft["candidates"] if c["id"] == candidate_id), None)
            if candidate is None:
                raise NotFound("candidate_not_found", "The draft has no candidate with that ID.")
            if candidate["kind"] == "gemini_voice" and not candidate["discarded"]:
                key = gemini_key(runtime)
                if candidate.get("project") != key_hash(key):
                    raise Conflict("candidate_other_project", "The candidate was made with a different Google API key.")
                try:
                    gemini_voices.delete_voice(key, candidate["provider_voice_id"])
                except AudioError as error:
                    raise ProviderFailure("provider_error", str(error)) from None

            def apply(current):
                for item in current["candidates"]:
                    if item["id"] == candidate_id:
                        item["discarded"] = True
            draft = runtime.voices.change_draft(draft_id, apply)
        finally:
            with runtime.store.lock:
                runtime.voice_busy.discard(draft_id)
        return draft_view(runtime, draft)

    @app.post("/api/voices/drafts/{draft_id}/abandon")
    def abandon(draft_id: str, request: Request):
        runtime = rt(request)
        draft = claim(runtime, draft_id)
        try:
            live = [c for c in draft["candidates"] if c["kind"] == "gemini_voice" and not c["discarded"]]
            key = gemini_key(runtime) if live else None
            if any(c.get("project") != key_hash(key) for c in live):
                # Another project's voice cannot be deleted with this key; a 404 would look like success.
                raise Conflict("candidate_other_project", "Some undiscarded candidates were made with a different "
                                                          "Google API key.")
            failures = delete_quietly(key, [c["provider_voice_id"] for c in live]) if live else []
            if failures:
                raise ProviderFailure("provider_error", f"Some stored Gemini candidates could not be deleted: {failures[0]}")

            def apply(current):
                current["status"] = "abandoned"
                for item in current["candidates"]:
                    item["discarded"] = True
            draft = runtime.voices.change_draft(draft_id, apply)
        finally:
            with runtime.store.lock:
                runtime.voice_busy.discard(draft_id)
        return draft_view(runtime, draft)

    @app.post("/api/voices/drafts/{draft_id}/save")
    def save(draft_id: str, body: SaveRequest, request: Request):
        runtime = rt(request)
        draft = runtime.voices.draft(draft_id)
        # Everything is validated before a Breeze server voice is uploaded.
        candidate = next((c for c in draft["candidates"] if c["id"] == body.candidate_id), None)
        if candidate is None:
            raise Invalid("unknown_candidate", "The draft has no candidate with the given candidate_id.")
        if candidate["discarded"]:
            raise Invalid("candidate_discarded", "The candidate was discarded.")
        name = body.name.strip()
        if not name:
            raise Invalid("voice_name_invalid", "The voice name must be 1–100 characters.")
        if body.mode == "version" and not draft.get("base_voice_id"):
            raise Invalid("draft_has_no_base_voice", "Only a draft started from an existing voice can save a new "
                                                     "version of it.")
        if body.make_default and draft["provider"] != "breeze":
            raise Invalid("default_breeze_only", "Only Breeze has a default voice.")
        if body.mode == "version":
            try:
                base = runtime.voices.voice(draft["base_voice_id"])
            except KeyError:
                base = None
            if base is None or base.get("deleted_at"):
                raise Conflict("base_voice_deleted", "The draft's base voice was deleted.")
            # A new current version re-voices every follower.
            require_no_narration(runtime, {row["book_id"] for row in followers(runtime, draft["base_voice_id"])})
        if body.make_default and runtime.narration_defaults().get("breeze"):
            # So does a new default, for every character on Default.
            require_no_narration(runtime, {row["book_id"] for row in followers(runtime, runtime.narration_defaults()["breeze"])})
        provider = draft["provider"]
        if provider == "breeze":
            config = breeze_config(runtime)
            clip = runtime.voices.asset_path(candidate["asset_id"]) if candidate.get("asset_id") else None
            if clip is None or not clip.is_file():
                raise Conflict("candidate_audio_missing", "The candidate's retained audio is missing, so it cannot "
                                                          "be uploaded.")
        else:
            key = gemini_key(runtime)
            if candidate.get("project") != key_hash(key):
                raise Conflict("candidate_other_project", "The candidate was made with a different Google API key.")
        draft = claim(runtime, draft_id)
        try:
            library_id = draft["base_voice_id"] if body.mode == "version" else runtime.voices.new_id()
            number = (max(v["version"] for v in base["versions"]) + 1 if body.mode == "version" else 1)
            recipe = {"description": candidate["description"], "sample_text": candidate.get("sample_text"),
                      "preview_id": candidate.get("preview_id"), "preview_seed": candidate.get("seed")}
            audition = ({"asset_id": candidate["asset_id"], "duration": candidate["duration"]}
                        if candidate.get("asset_id") else None)
            pinned = None
            if provider == "breeze":
                # Upload the exact clip that was auditioned as a cloned voice, so
                # saving survives preview expiry. The server re-encodes its stored
                # reference (verified live), so the pin hashes what it keeps and
                # the library keeps the auditioned clip as this version's audition.
                pinned = breeze.clone(config, voice_id=f"bardic-{uuid4().hex[:8]}", name=name,
                                      audio=clip.read_bytes(),
                                      filename="preview.wav", reference_text=candidate["sample_text"],
                                      description=candidate["description"],
                                      labels={"bardic_voice": library_id, "bardic_version": str(number)})
                version = {"provider_voice_id": pinned["id"], "revision": pinned["revision"], "seed": pinned["seed"],
                           "made": "designed", "recipe": recipe, "audition": audition}
            else:
                recipe.update(model=candidate.get("model"), language_code=candidate.get("language_code"),
                              gender=candidate.get("gender"))
                version = {"provider_voice_id": candidate["provider_voice_id"], "made": "designed", "recipe": recipe,
                           "audition": audition, "project": candidate.get("project"), "expires_at": candidate.get("expires_at")}
            try:
                if body.mode == "version":
                    voice = runtime.voices.add_version(library_id, version)
                else:
                    voice = runtime.voices.create(provider, name=name, description=candidate["description"],
                                                  origin="designed", version=version, source=draft.get("context"),
                                                  voice_id=library_id)
            except Exception as error:
                note = undo_upload(config, pinned) if pinned else ""
                if isinstance(error, NotFound) and body.mode == "version":
                    raise Conflict("base_voice_deleted", "The draft's base voice was deleted while saving." + note) from None
                if isinstance(error, ApiError):
                    error.detail += note
                raise
            if pinned:
                note_breeze_voices(runtime, added={**pinned, "name": name, "description": candidate["description"]})
            cleanup = []
            if provider == "gemini":
                # Unchosen candidates are stored, billed voices; remove them from the project.
                unchosen = [c for c in draft["candidates"] if c["kind"] == "gemini_voice" and not c["discarded"]
                            and c["id"] != candidate["id"]]
                cleanup = delete_quietly(key, [c["provider_voice_id"] for c in unchosen if c.get("project") == key_hash(key)])
                if any(c.get("project") != key_hash(key) for c in unchosen):
                    cleanup.append("Some unchosen candidates were made with a different Google API key and remain stored in that project.")

            def apply(current):
                current["status"] = "saved"
                current["saved"] = {"voice_id": voice["id"], "version": voice["current_version"], "candidate_id": candidate["id"]}
            runtime.voices.change_draft(draft_id, apply)
        except AudioError as error:
            raise ProviderFailure("provider_error", str(error)) from None
        finally:
            with runtime.store.lock:
                runtime.voice_busy.discard(draft_id)
        if body.make_default:
            set_default(runtime, "breeze", voice["id"])
        result = {"voice": voice_view(runtime, runtime.voices.voice(voice["id"]))}
        if cleanup:
            result["cleanup_error"] = cleanup[0]
        if body.assign:
            book, error = assign(runtime, body.assign, provider, voice["id"])
            if book is not None:
                result["book"] = book
            if error:
                # The voice exists (and may have been paid for); report, never roll back.
                result["assignment_error"] = error
        return result

    @app.post("/api/voices/breeze/clone")
    def clone(request: Request, name: str = Form(..., max_length=100), reference_text: str = Form(..., max_length=2000),
              consent: str = Form(...), description: str = Form("", max_length=1000),
              book_id: str | None = Form(None), character_id: str | None = Form(None),
              reference_audio: UploadFile = File(...)):
        runtime = rt(request)
        # Everything is validated before the server voice is created.
        if consent != "true":
            raise Invalid("consent_required", "Cloning a voice needs consent set to true, confirming the speaker's "
                                              "consent.")
        if not name.strip():
            raise Invalid("voice_name_invalid", "The voice name must be 1–100 characters.")
        if not reference_text.strip():
            raise Invalid("reference_text_missing", "The transcript of the recording is empty.")
        data = reference_audio.file.read(breeze.MAX_REFERENCE_BYTES + 1)
        if not data:
            raise Invalid("recording_empty", "The recording is empty.")
        if len(data) > breeze.MAX_REFERENCE_BYTES:
            raise TooLarge("recording_too_large", "The recording is larger than 20 MB.")
        source = None
        if book_id and character_id:
            book, character, _ = character_context(runtime, book_id, character_id)
            source = {"book_id": book["id"], "character_id": character["id"], "character_name": character.get("name")}
        config = breeze_config(runtime)
        library_id = runtime.voices.new_id()
        try:
            pinned = breeze.clone(config, voice_id=f"bardic-{uuid4().hex[:8]}", name=name.strip(), audio=data,
                                  filename=reference_audio.filename or "recording", reference_text=reference_text,
                                  description=description, labels={"bardic_voice": library_id, "bardic_version": "1"})
            try:
                audition = runtime.voices.store_audio(breeze.reference_audio(config, pinned["id"]))
            except AudioError:
                audition = None
        except AudioError as error:
            raise ProviderFailure("provider_error", str(error)) from None
        try:
            voice = runtime.voices.create("breeze", name=name, description=description, origin="cloned", voice_id=library_id,
                                          version={"provider_voice_id": pinned["id"], "revision": pinned["revision"],
                                                   "seed": pinned["seed"], "made": "cloned",
                                                   "recipe": {"description": description, "sample_text": reference_text.strip()},
                                                   "audition": audition}, source=source)
        except Exception as error:
            note = undo_upload(config, pinned)
            if isinstance(error, ApiError):
                error.detail += note
            raise
        note_breeze_voices(runtime, added={**pinned, "name": name.strip(), "description": description})
        result = {"voice": voice_view(runtime, voice)}
        if source:
            book, error = assign(runtime, Assignment(book_id=book_id, character_id=character_id), "breeze", voice["id"])
            if book is not None:
                result["book"] = book
            if error:
                result["assignment_error"] = error
        return result

    return {"library_view": library_view, "set_default": set_default}


def import_breeze_voices(runtime, set_default) -> None:
    """Make every usable server voice a library voice, and choose an initial default.

    Voices already behind any library version (including deleted library
    voices, which the owner removed on purpose) are not imported again.
    """
    with runtime.store.lock:
        view = runtime.breeze_view()
        config = runtime.breeze_config()
    if view["state"] != "ready":
        return
    for server in view["voices"]:
        if not server["usable"] or runtime.voices.find_provider_voice("breeze", server["id"]):
            continue
        try:
            audition = runtime.voices.store_audio(breeze.reference_audio(config, server["id"]))
        except AudioError:
            audition = None
        pin = breeze.pin(view, server["id"])
        # A server name is not user input: fit it to the library limit (code points) instead of refusing it.
        name = server["name"][:MAX_NAME].strip() or server["id"]
        runtime.voices.create("breeze", name=name, description=server["description"][:MAX_DESCRIPTION], origin="imported",
                              version={"provider_voice_id": pin["id"], "revision": pin["revision"], "seed": pin["seed"],
                                       "made": "imported", "recipe": {"description": server["description"]},
                                       "audition": audition})
    if not runtime.narration_defaults().get("breeze"):
        preferred = view.get("default_voice_id")
        matches = runtime.voices.find_provider_voice("breeze", preferred) if preferred else []
        live = [voice_id for voice_id, _ in matches if not runtime.voices.voice(voice_id).get("deleted_at")]
        if not live:
            live = [voice["id"] for voice in runtime.voices.voices() if voice["provider"] == "breeze"]
        if live:
            set_default(runtime, "breeze", live[0])
