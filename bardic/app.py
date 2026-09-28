"""Local API, resumable production worker, and audiobook exports."""
from __future__ import annotations

import copy
import json
import os
import re
import shutil
import tempfile
import threading
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Literal
from urllib.parse import urlparse
from uuid import uuid4

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, model_validator
from starlette.background import BackgroundTask
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .analysis import analyze_book
from .apispec import install as install_contract
from .processing import BudgetReached
from .account_checks import check_account
from . import breeze, local_services, pronunciation
from .audio import (BREEZE_MODEL, PROVIDERS as NARRATION_PROVIDERS, AudioError, assemble_audio, list_system_voices,
                    providers_status, render_fingerprint, synthesize, validate_audio, voice_id, voice_selection)
from .voice_library import VoiceLibrary, assignments, concrete_selection, library_reference
from .voice_routes import import_breeze_voices, register as register_voice_routes
from .config import data_directory
from .errors import STATUS_CODES, ApiError, NotFound
from .diagnostics import DiagnosticRepository, IDENTIFIERS, record_safely
from .importer import make_demo_book, parse_book
from .lan import allowed_hosts
from .model_catalog import ANALYSIS_CATALOG, PREPROCESS_DEFAULTS, ModelCatalog
from .pipeline import default_registry
from .pipeline.api import build_router as pipeline_router
from .pipeline.repository import PipelineRepository
from .series import SeriesRepository
from .structure import repair_structure, transform_checkpoint_structure
from .store import InstanceLock, Store
from .take_archive import produce_take
from .chapter_listening import ChapterCoordinator, QuotaReached
from .chunking import Calibration, normalize_options, plan as plan_chunks
from .tts_limits import CANCEL_CHECK, DEFAULT_LIMITS as DEFAULT_TTS_LIMITS, LIMITER, normalize_limits, quota_day, requests_today

TTS_MODELS = ["gemini-3.8-flash-tts", "gemini-3.8-flash-lite-tts", "gemini-3.1-flash-tts-preview"]
ANALYSIS_MODELS = ANALYSIS_CATALOG["gemini"]
ANALYSIS_LABELS = {"local": "Local draft", "gemini": "Gemini", "openai": "OpenAI", "anthropic": "Anthropic"}
ACCOUNT_LINKS = {
    "gemini": {"billing_url": "https://aistudio.google.com/billing", "usage_url": "https://aistudio.google.com/usage"},
    "openai": {"billing_url": "https://platform.openai.com/settings/organization/billing", "usage_url": "https://platform.openai.com/usage"},
    "anthropic": {"billing_url": "https://platform.claude.com/settings/billing", "usage_url": "https://platform.claude.com/usage"},
}
STATIC = Path(__file__).parent / "static"
ACTIVE = {"queued", "running"}


class Cancelled(Exception):
    pass


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RenderRequest(StrictModel):
    provider: str = "system"
    scene_id: str | None = None
    segment_id: str | None = None
    force: bool = False


class AnalysisLimits(StrictModel):
    max_requests: int = Field(default=25, ge=1, le=1000)
    max_input_tokens: int = Field(default=1000000, ge=1000, le=10000000)
    max_output_tokens: int = Field(default=100000, ge=1000, le=2000000)
    budget_usd: float | None = Field(default=1.0, gt=0, le=1000, allow_inf_nan=False)


class AnalysisRequest(StrictModel):
    provider: str | None = None
    chapter_id: str | None = None
    resume: bool = True
    phase: Literal["scan", "profiles", "direct", "full"] = "scan"
    limits: AnalysisLimits = Field(default_factory=AnalysisLimits)


class SeriesProcessingRequest(StrictModel):
    provider: str | None = None
    phase: Literal['scan', 'profiles', 'direct', 'full'] = 'scan'
    concurrency: int = Field(default=2, ge=1, le=2)
    limits: AnalysisLimits = Field(default_factory=AnalysisLimits)
    expected_plan_fingerprint: str | None = Field(default=None, max_length=64)


class BookMetadataRequest(StrictModel):
    title: str = Field(min_length=1, max_length=500)
    author: str = Field(default='', max_length=500)


class SeriesVolumeRequest(StrictModel):
    position: float = Field(ge=0, le=1000000, allow_inf_nan=False)
    title: str = Field(default='', max_length=500)
    status: Literal['missing', 'planned'] = 'missing'


class ListenRequest(StrictModel):
    provider: Literal['system', 'gemini', 'breeze'] = 'system'
    voice: str | None = Field(default=None, max_length=256)
    model: str | None = Field(default=None, max_length=200)
    segment_id: str


class ChunkingOptions(StrictModel):
    ramp_seconds: list[Annotated[float, Field(ge=10, le=470, allow_inf_nan=False)]] | None = Field(default=None, max_length=6)
    target_seconds: float | None = Field(default=None, ge=30, le=470, allow_inf_nan=False)
    concurrency: int | None = Field(default=None, ge=1, le=3)


class ChapterListenRequest(StrictModel):
    provider: Literal['gemini'] = 'gemini'
    voice: str | None = Field(default=None, max_length=256)
    model: str | None = Field(default=None, max_length=200)
    segment_id: str = Field(max_length=200)
    intent: Literal['play', 'queue'] = 'queue'
    chunking: ChunkingOptions | None = None


class PerformanceRequest(StrictModel):
    name: str | None = Field(default=None, max_length=200)
    mode: Literal['simple', 'cast']
    chapter_ids: list[Annotated[str, Field(max_length=200)]] = Field(min_length=1, max_length=5000)
    provider: Literal['system', 'gemini', 'breeze']
    voice: str | None = Field(default=None, max_length=256)
    model: str | None = Field(default=None, max_length=200)


class PerformanceEdit(StrictModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    archived: bool | None = None


class PronunciationEntry(StrictModel):
    """A book pronunciation; bardic.pronunciation validates content beyond these bounds."""
    id: str | None = Field(default=None, max_length=40)
    term: str = Field(max_length=200)
    respelling: str = Field(max_length=300)
    providers: dict[Literal['system', 'gemini', 'breeze'], str | None] | None = None
    match_case: bool = True
    character_id: str | None = Field(default=None, max_length=200)
    note: str | None = Field(default=None, max_length=500)


class VoicePreviewRequest(StrictModel):
    provider: Literal['system', 'gemini', 'breeze'] = 'system'
    voice: str | None = Field(default=None, max_length=256)
    model: str | None = Field(default=None, max_length=200)
    segment_id: str | None = Field(default=None, max_length=200)
    character_id: str | None = Field(default=None, max_length=200)
    direction: str | None = Field(default=None, max_length=3000)
    segment_direction: str | None = Field(default=None, max_length=3000)
    # An unsaved pronunciation to audition in place of the entry it edits.
    pronunciation: PronunciationEntry | None = None


class DiagnosticRequest(StrictModel):
    event: Literal['listen_request_failed', 'listen_poll_failed', 'listen_job_failed',
                   'buffer_failed', 'cache_read_failed', 'playback_media_error',
                   'playback_play_rejected', 'playback_waiting', 'playback_resumed', 'preview_failed']
    book_id: str | None = Field(default=None, pattern='^' + IDENTIFIERS['book_id'] + '$', max_length=36)
    segment_id: str | None = Field(default=None, pattern='^' + IDENTIFIERS['segment_id'] + '$', max_length=40)
    session_id: str | None = Field(default=None, pattern='^' + IDENTIFIERS['session_id'] + '$', max_length=64)
    job_id: str | None = Field(default=None, pattern='^' + IDENTIFIERS['job_id'] + '$', max_length=32)
    playback_rate: float | None = Field(default=None, strict=True, ge=.1, le=8, allow_inf_nan=False)
    http_status: int | None = Field(default=None, strict=True, ge=100, le=599)
    media_error_code: int | None = Field(default=None, strict=True, ge=1, le=4)
    operation: Literal['request', 'poll', 'play', 'prefetch', 'media', 'prepare', 'settle', 'cache_read'] | None = None


class SettingsRequest(StrictModel):
    api_key: str | None = Field(default=None, max_length=500)
    tts_model: str | None = None
    analysis_model: str | None = None
    api_keys: dict[str, Annotated[str, Field(max_length=500)]] | None = None
    analysis_models_by_provider: dict[str, str] | None = None
    preprocess_models_by_provider: dict[str, str] | None = None
    analysis_provider: str | None = None
    tts_limits: dict[str, dict[str, int]] | None = None
    listen_chunking: ChunkingOptions | None = None
    breeze_url: str | None = Field(default=None, max_length=500)
    breeze_api_key: str | None = Field(default=None, max_length=500)
    # Self-hosted analysis servers by provider ID (see local_services.SERVICES); "" clears one.
    local_service_urls: dict[str, Annotated[str, Field(max_length=500)]] | None = None


def valid_analysis_model(model):
    return isinstance(model, str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,199}", model) is not None


class VoiceChoice(StrictModel):
    """Exactly one of ``id`` (a provider voice) or ``library`` (follow a library voice)."""
    id: str | None = Field(default=None, min_length=1, max_length=200)
    library: str | None = Field(default=None, pattern=r"^vl_[a-f0-9]{16}$")
    seed: int | None = Field(default=None, ge=0, le=breeze.MAX_SEED)

    @model_validator(mode="after")
    def one_source(self):
        if (self.id is None) == (self.library is None):
            raise ValueError("Choose either a provider voice id or a library voice")
        return self


class CharacterEdit(StrictModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    aliases: list[str] | None = None
    description: str | None = Field(default=None, max_length=3000)
    # One saved choice per narration provider; null removes that provider's choice.
    voices: dict[str, VoiceChoice | None] | None = None
    # Earlier single-provider fields, accepted and stored as voices.gemini/voices.system.
    voice: str | None = Field(default=None, max_length=200)
    system_voice: str | None = Field(default=None, max_length=200)
    direction: str | None = Field(default=None, max_length=3000)


class SegmentEdit(StrictModel):
    speaker_id: str | None = None
    direction: str | None = Field(default=None, max_length=3000)
    cues: list[str] | None = None
    # A seeded provider (Breeze) repeats a take for the same seed; a new seed is a new take.
    seed: int | None = Field(default=None, ge=0, le=breeze.MAX_SEED)


class SceneEdit(StrictModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    summary: str | None = Field(default=None, max_length=4000)
    tone: str | None = Field(default=None, max_length=1000)
    direction: str | None = Field(default=None, max_length=3000)


class SeriesNameRequest(StrictModel):
    name: str = Field(min_length=1, max_length=200)


class SeriesMembershipRequest(StrictModel):
    series_id: str | None = Field(default=None, max_length=200)
    position: Annotated[float, Field(strict=True)] | None = None


class SeriesCharacterLinkRequest(StrictModel):
    series_character_id: str | None = Field(default=None, max_length=200)


class Runtime:
    def __init__(self, root: Path):
        self.instance_lock = InstanceLock(root.resolve())
        self.store = Store(root)
        PipelineRepository(self.store).recover_interrupted()
        self.voices = VoiceLibrary(self.store)
        # Voice drafts with a generation request in flight (a per-draft lock).
        self.voice_busy: set[str] = set()
        self.voice_import_lock = threading.Lock()
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="bardic")
        self.series_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix='series-coordinator')
        # Chapter listening has its own coordinator and bounded request pool so
        # a long chapter does not block analysis or other books' work.
        self.listen_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix='listen-coordinator')
        self.narration_pool = ThreadPoolExecutor(max_workers=3, thread_name_prefix='narration')
        # Saved performances run one at a time; a Gemini chapter inside one
        # sends its requests through the narration pool like live listening.
        self.performance_pool = ThreadPoolExecutor(max_workers=3, thread_name_prefix='performance')
        from .performances import PerformanceRepository
        PerformanceRepository(self.store)
        self.stopping = threading.Event()
        self.api_keys = {
            "gemini": os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY") or "",
            "openai": os.environ.get("OPENAI_API_KEY") or "",
            "anthropic": os.environ.get("ANTHROPIC_API_KEY") or "",
        }
        # Narration-only credentials, kept apart from the analysis provider keys.
        self.narration_keys = {"breeze": os.environ.get("BREEZE_API_KEY") or ""}
        self.account_checks = {}
        self.account_check_locks = {provider: threading.Lock() for provider in ANALYSIS_CATALOG}
        self.account_checks_running = {}
        self.model_catalog = ModelCatalog()
        saved = self.store.settings()
        models = {provider: choices[0] for provider, choices in ANALYSIS_CATALOG.items()}
        # Keep the existing Gemini selection when upgrading an older library.
        if valid_analysis_model(saved.get("analysis_model")):
            models["gemini"] = saved["analysis_model"]
        for provider, model in saved.get("analysis_models_by_provider", {}).items():
            if provider in models and valid_analysis_model(model):
                models[provider] = model
        preprocess_models = dict(PREPROCESS_DEFAULTS)
        for provider, model in saved.get("preprocess_models_by_provider", {}).items():
            if provider in preprocess_models and valid_analysis_model(model):
                preprocess_models[provider] = model
        self.preferences = {
            "tts_model": saved.get("tts_model", TTS_MODELS[0]),
            "analysis_provider": saved.get("analysis_provider", "local") if saved.get("analysis_provider", "local") in ANALYSIS_LABELS else "local",
            "analysis_model": models["gemini"],
            "analysis_models_by_provider": models,
            "preprocess_models_by_provider": preprocess_models,
            "tts_limits": self._saved_tts_limits(saved.get("tts_limits")),
            "listen_chunking": self._saved_chunking(saved.get("listen_chunking")),
            "breeze_url": self._saved_breeze_url(saved.get("breeze_url")),
            "local_service_urls": self._saved_service_urls(saved.get("local_service_urls")),
            # The last voice check survives restarts so pinned sessions and
            # cached audio resolve while the server is offline.
            "breeze_catalog": saved.get("breeze_catalog") if isinstance(saved.get("breeze_catalog"), dict) else None,
            # Characters with no Breeze choice follow this library voice.
            "narration_defaults": {"breeze": (saved.get("narration_defaults") or {}).get("breeze")
                                   if isinstance(saved.get("narration_defaults"), dict) else None},
            # Last listing of the Google project's stored voices (metadata only).
            "gemini_voice_catalog": saved.get("gemini_voice_catalog") if isinstance(saved.get("gemini_voice_catalog"), dict) else None,
        }
        LIMITER.configure(self.preferences["tts_limits"])
        self.breeze_checking = False

    @staticmethod
    def _saved_breeze_url(saved):
        for value in (saved, os.environ.get("BREEZE_TTS_URL")):
            if isinstance(value, str) and value.strip():
                try:
                    return breeze.normalize_base_url(value)
                except ValueError:
                    continue
        return ""

    @staticmethod
    def _saved_service_urls(saved):
        """Only URLs set in Settings ("" = cleared there). The environment is resolved at use and never saved,
        so a development server's `--keys` URLs do not outlive its restart."""
        saved = saved if isinstance(saved, dict) else {}
        result = {}
        for provider in local_services.SERVICES:
            value = saved.get(provider)
            if isinstance(value, str):
                try:
                    result[provider] = local_services.normalize_url(value, provider)
                except ValueError:
                    continue
        return result

    def service_urls(self):
        """Each self-hosted server's URL: the Settings value if one was saved (even ""), else the environment."""
        saved = self.preferences["local_service_urls"]
        result = {}
        for provider, service in local_services.SERVICES.items():
            if provider in saved:
                result[provider] = saved[provider]
                continue
            try:
                result[provider] = local_services.normalize_url(os.environ.get(service["env"]) or "", provider)
            except ValueError:
                result[provider] = ""
        return result

    def analysis_credentials(self):
        """What a pipeline job snapshots per provider: a cloud API key, or a self-hosted server URL."""
        return {**self.api_keys, **self.service_urls()}

    def breeze_config(self):
        return {"base_url": self.preferences["breeze_url"], "api_key": self.narration_keys["breeze"]}

    def breeze_view(self):
        """Report the last Breeze check without contacting the server."""
        config = self.breeze_config()
        catalog = self.preferences.get("breeze_catalog") or {}
        current = catalog.get("base_url") == config["base_url"] and bool(config["base_url"])
        if not config["base_url"]:
            state, message = "unconfigured", "Add the Breeze server URL to narrate with Breeze."
        elif self.breeze_checking:
            state, message = "checking", "Checking the Breeze server…"
        elif not current:
            state, message = "unchecked", "Check the connection to load Breeze voices."
        else:
            state, message = catalog.get("state", "unchecked"), catalog.get("message", "")
        return {"configured": bool(config["base_url"]), "base_url": config["base_url"],
                "has_api_key": bool(config["api_key"]), "state": state, "message": message,
                "checked_at": catalog.get("checked_at") if current else None,
                "model": catalog.get("model") if current else None,
                "default_voice_id": catalog.get("default_voice_id") if current else None,
                "voices": copy.deepcopy(catalog.get("voices", [])) if current else []}

    def refresh_breeze(self):
        """Check the server and voices outside the store lock; free, no generation."""
        with self.store.lock:
            config = self.breeze_config()
            if not config["base_url"]:
                raise HTTPException(400, "Add the Breeze server URL in Settings first.")
            if self.breeze_checking:
                raise HTTPException(409, "A Breeze check is already running.")
            self.breeze_checking = True
        try:
            result = breeze.fetch_catalog(config)
        except Exception:
            # Unexpected transport failures can carry request details.
            result = {"state": "error", "message": "The Breeze check could not finish. Try again.",
                      "model": None, "voices": [], "default_voice_id": None}
        finally:
            with self.store.lock:
                self.breeze_checking = False
        with self.store.lock:
            if config != self.breeze_config():
                raise HTTPException(409, "Breeze settings changed during the check. Check the connection again.")
            previous = self.preferences.get("breeze_catalog") or {}
            if result["state"] != "ready" and previous.get("base_url") == config["base_url"]:
                # Keep the last known voices so pinned choices and cached audio still resolve.
                result = {**result, "voices": previous.get("voices", []),
                          "default_voice_id": previous.get("default_voice_id")}
            preferences = copy.deepcopy(self.preferences)
            preferences["breeze_catalog"] = {**result, "base_url": config["base_url"],
                                             "checked_at": datetime.now(timezone.utc).isoformat()}
            self.store.save_settings(preferences)
            self.preferences = preferences
            return self.breeze_view()

    def narrator_choice(self, provider, voice):
        """Resolve a listen/preview voice value to (voice id, Breeze pin). Local only.

        ``voice`` is "library:<id>" (that voice's current version), a direct
        provider voice id, or empty for Default. Breeze's Default is the Bardic
        default voice, the same one characters without a choice follow.
        """
        reference = voice[len("library:"):] if isinstance(voice, str) and voice.startswith("library:") else None
        if reference is not None or (provider == "breeze" and not voice):
            if provider not in ("breeze", "gemini"):
                raise HTTPException(400, "Library voices are Breeze or Gemini voices.")
            resolved = self.resolve_choice(provider, {"library": reference} if reference else None)
            if resolved is None or resolved.get("error"):
                raise HTTPException(400, (resolved or {}).get("error") or "Choose a voice first.")
            return resolved["id"], (resolved if provider == "breeze" else None)
        if provider == "breeze":
            return voice, self.breeze_selection(voice)
        return voice, None

    def breeze_selection(self, voice, seed=None):
        """Pin a Breeze voice from the saved check. Local only; call under the store lock."""
        view = self.breeze_view()
        try:
            return breeze.pin(view, voice, seed=seed)
        except ValueError as error:
            raise HTTPException(400, str(error)) from None

    def narration_credentials(self, provider):
        if provider == "gemini":
            return self.api_keys["gemini"]
        if provider == "breeze":
            return self.breeze_config()
        return None

    def narration_secrets(self, provider):
        key = {"gemini": self.api_keys["gemini"], "breeze": self.narration_keys["breeze"]}.get(provider)
        return (key,) if key else ()

    def require_breeze(self):
        if not self.preferences["breeze_url"]:
            raise HTTPException(400, "Add the Breeze server URL in Settings first, or choose another narrator.")

    @staticmethod
    def _saved_tts_limits(saved):
        limits = {model: dict(DEFAULT_TTS_LIMITS) for model in TTS_MODELS}
        for model, value in (saved or {}).items() if isinstance(saved, dict) else ():
            if model in limits:
                try:
                    limits[model] = normalize_limits(value)
                except ValueError:
                    pass
        return limits

    @staticmethod
    def _saved_chunking(saved):
        try:
            return normalize_options(saved if isinstance(saved, dict) else None)
        except ValueError:
            return normalize_options(None)

    @property
    def api_key(self):
        """Compatibility alias for the Gemini narration key."""
        return self.api_keys["gemini"]

    @api_key.setter
    def api_key(self, value):
        self.api_keys["gemini"] = value

    def close(self):
        self.stopping.set()
        self.series_pool.shutdown(wait=True, cancel_futures=True)
        self.listen_pool.shutdown(wait=True, cancel_futures=True)
        self.performance_pool.shutdown(wait=True, cancel_futures=True)
        self.narration_pool.shutdown(wait=True, cancel_futures=True)
        self.pool.shutdown(wait=True, cancel_futures=True)
        self.instance_lock.close()

    def account_check_view(self, provider):
        """Report prior checks without sending any provider requests."""
        model = self.preferences["analysis_models_by_provider"][provider]
        key = self.api_keys[provider]
        configuration = (key, model)
        base = {"provider": provider, "model": model, "state": "unchecked" if key else "missing_key",
                "message": "Run a small request to check this analysis model." if key else "Add an API key to check this account.",
                "checked_at": None, "usage": None, "http_status": None,
                "balance": None, "balance_note": "Exact balance is not available through this check. Open the billing dashboard.",
                "cached": False, **ACCOUNT_LINKS[provider]}
        stored = self.account_checks.get(provider)
        if stored and stored["configuration"] == configuration:
            base.update(copy.deepcopy(stored["result"]))
        if self.account_checks_running.get(provider) == configuration:
            base.update(state="checking", message="Checking this analysis model…")
        return base

    def check_account(self, provider):
        if provider not in ANALYSIS_CATALOG:
            raise HTTPException(400, "Choose gemini, openai, or anthropic")
        gate = self.account_check_locks[provider]
        if not gate.acquire(blocking=False):
            raise HTTPException(409, "A check for this provider is already running")
        try:
            with self.store.lock:
                key = self.api_keys[provider]
                model = self.preferences["analysis_models_by_provider"][provider]
                configuration = (key, model)
                previous = self.account_checks.get(provider)
                if previous and previous["configuration"] == configuration and time.monotonic() - previous["time"] < 30:
                    return {**self.account_check_view(provider), "cached": True}
                result = self.account_check_view(provider)
                self.account_checks_running[provider] = configuration
            try:
                outcome = check_account(provider, key, model)
            except Exception:
                # Do not expose unexpected transport exceptions or credentials.
                outcome = {"state": "provider_error", "message": "The account check could not finish. Try again shortly.", "usage": None, "http_status": None}
            result.update(outcome, checked_at=datetime.now(timezone.utc).isoformat(), cached=False)
            with self.store.lock:
                if configuration != (self.api_keys[provider], self.preferences["analysis_models_by_provider"][provider]):
                    # A slow response for a replaced credential must not look current.
                    raise HTTPException(409, "Provider settings changed during the check. Check the current settings again.")
                self.account_checks[provider] = {"configuration": configuration, "time": time.monotonic(), "result": copy.deepcopy(result)}
            return result
        finally:
            with self.store.lock:
                self.account_checks_running.pop(provider, None)
            gate.release()

    def cancelled(self, job_id):
        return self.stopping.is_set() or self.store.job(job_id).get("cancel_requested", False)

    def check_cancel(self, job_id):
        if self.cancelled(job_id):
            raise Cancelled()

    def assign_local_voices(self, book):
        installed = list_system_voices()
        choices = []
        for preferred in ("Samantha", "Daniel", "Moira", "Karen", "Tessa", "Alex", "Fred"):
            match = next((v["id"] for v in installed if v["id"].split(" (")[0] == preferred), None)
            if match:
                choices.append(match)
        if not choices:
            choices = [v["id"] for v in installed if v["locale"].startswith("en-")][:8]
        if choices:
            for index, character in enumerate(book["characters"]):
                if not voice_id(character, "system"):
                    character.setdefault("voices", {})["system"] = {"id": choices[index % len(choices)]}

    def require_idle(self, book_id):
        from .errors import Conflict
        self.store.require_active(book_id)
        # Every active job counts: a long run can have more than 100 newer child jobs.
        if any(j["status"] in ACTIVE for j in self.store.jobs(book_id, limit=None, active=True)):
            raise Conflict("job_active", "A job is working on this book.")
        if any(j['kind'] == 'series' and j['status'] in ACTIVE and book_id in j.get('book_ids', [])
               for j in self.store.jobs(limit=None)):
            raise Conflict('series_run_active', 'An active series run has reserved this book.')

    def audio_path(self, book_id, audio_id):
        if not re.fullmatch(r"[a-zA-Z0-9_-]+", book_id) or not re.fullmatch(r"[a-f0-9]{32,128}", audio_id):
            raise ValueError("Invalid audio identifier")
        return self.store.root / "audio" / book_id / f"{audio_id}.wav"

    def take_path(self, book_id, metadata):
        return self.audio_path(book_id, metadata.get("asset_id") or metadata["fingerprint"])

    # Voice resolution -------------------------------------------------
    # Cast assignments may name a library voice (following its current
    # version) or rely on the Breeze default. These helpers turn them into
    # concrete provider voices from local SQLite state only, so takes validate
    # offline. An unresolvable assignment becomes {"error": ...}, which the
    # recipe refuses instead of falling back to a provider default voice.

    def narration_defaults(self):
        return dict(self.preferences.get("narration_defaults") or {})

    def library_index(self):
        return {voice["id"]: voice for voice in self.voices.voices(include_deleted=True)}

    def resolve_choice(self, provider, selection, index=None):
        if selection is None:
            if provider != "breeze":
                return None
            default = self.narration_defaults().get("breeze")
            if not default:
                return {"error": "Choose a Breeze voice for this character, or set a default Breeze voice in Voices."}
            selection = {"library": default}
        reference = library_reference(selection)
        if reference is None:
            return selection
        index = self.library_index() if index is None else index
        try:
            return concrete_selection(index.get(reference), provider)
        except AudioError as error:
            return {"error": str(error), "library": reference}

    def effective_character(self, character, index=None):
        """A copy of a cast character whose Breeze and Gemini voices are concrete."""
        index = self.library_index() if index is None else index
        voices = dict(character.get("voices")) if isinstance(character.get("voices"), dict) else {}
        for provider in ("breeze", "gemini"):
            selection = voices.get(provider) if isinstance(voices.get(provider), dict) else None
            if selection is None and provider == "gemini":
                continue  # Legacy field or the recipe's own default.
            resolved = self.resolve_choice(provider, selection, index)
            if resolved is not None:
                voices[provider] = resolved
        return {**character, "voices": voices}

    def resolved_cast(self, book):
        index = self.library_index()
        return {c["id"]: self.effective_character(c, index) for c in book["characters"]}

    def valid_audio(self, book, segment, cast=None):
        metadata = segment.get("audio")
        if not metadata:
            return False
        try:
            characters = self.resolved_cast(book) if cast is None else cast
            scenes = {s["id"]: s for s in book["scenes"]}
            expected = render_fingerprint(pronunciation.with_lexicon(segment, pronunciation.book_lexicon(book)),
                                          characters[segment["speaker_id"]], scenes[segment["scene_id"]], metadata["provider"], metadata["model"])
            return expected == metadata.get("fingerprint") and self.take_path(book["id"], metadata).is_file()
        except (ValueError, KeyError, TypeError):
            return False

    def playable_count(self, book):
        """Passages whose selected enhanced take is current and on disk (the ones `present` gives a URL)."""
        cast = self.resolved_cast(book)
        return sum(self.valid_audio(book, s, cast) for s in book["segments"])

    def present(self, book):
        result = copy.deepcopy(book)
        chapter_map = {c["id"]: c for c in result["chapters"]}
        previous = {}
        cast = self.resolved_cast(book)
        for s in result["segments"]:
            chapter = chapter_map[s["chapter_id"]]
            s["leading_text"] = chapter["text"][previous.get(chapter["id"], 0):s["start"]]
            previous[chapter["id"]] = s["end"]
            if s.get("audio") and self.valid_audio(book, s, cast):
                audio_id = s["audio"].get("asset_id") or s["audio"]["fingerprint"]
                s["audio"]["url"] = f"/api/audio/{book['id']}/{s['id']}?v={audio_id[:16]}"
            else:
                s["audio"] = None
        for c in result["chapters"]:
            c["trailing_text"] = c["text"][previous.get(c["id"], 0):]
        for character in result["characters"]:
            character["voices"] = assignments(character)
        return result

    def merge_voices(self, item, fields):
        """Apply per-provider voice choices to a character; call under the store lock.

        Earlier single-provider fields are accepted and saved as voices entries,
        leaving one stored source for each provider's choice. Breeze choices are
        pinned to the voice revision from the last server check.
        """
        changes = dict(fields.pop("voices", None) or {})
        for provider, legacy in (("gemini", "voice"), ("system", "system_voice")):
            if legacy in fields:
                value = fields.pop(legacy)
                changes.setdefault(provider, {"id": value} if isinstance(value, str) and value.strip() else None)
        if not changes:
            return
        if set(changes) - set(NARRATION_PROVIDERS):
            raise HTTPException(400, "Choose voices for system, gemini or breeze narration")
        voices = assignments(item)
        for provider, choice in changes.items():
            if choice is None:
                voices.pop(provider, None)
            elif choice.get("library"):
                voice = self.library_index().get(choice["library"])
                if not voice or voice.get("deleted_at") or voice["provider"] != provider:
                    raise HTTPException(400, f"Choose a {provider.title()} voice from the voice library.")
                voices[provider] = {"library": voice["id"]}
            elif provider == "breeze":
                voices[provider] = self.breeze_selection(choice["id"].strip(), seed=choice.get("seed"))
            else:
                voices[provider] = {"id": choice["id"].strip()}
        fields["voices"] = voices
        item.pop("voice", None)
        item.pop("system_voice", None)

    def run(self, job, operation, secrets=()):
        job_id = job["id"]
        # Paced provider waits inside this job check its cancellation and shutdown.
        cancel_token = CANCEL_CHECK.set(lambda: self.check_cancel(job_id))
        try:
            self.check_cancel(job_id)
            self.store.update_job(job_id, status="running", message="Starting…")
            operation()
            self.check_cancel(job_id)
            self.store.update_job(job_id, status="completed", message=(
                "Chapter ready to listen" if job["kind"] == "listen_chapter" else
                "Performance ready" if job["kind"] == "performance" else
                "Ready to listen" if job["kind"] in {"render", "listen", "voice_preview"} else "Analysis ready for review"))
        except BudgetReached as exc:
            self.store.update_job(job_id, status="budget_limited", message=str(exc))
        except QuotaReached as exc:
            self.store.update_job(job_id, status="quota_limited", message=str(exc), resume_after=exc.resume_after)
        except (Cancelled, InterruptedError):
            message = ("Stopped. Validated chapter work is saved; analyze again to resume." if job["kind"] in {"analyze", "pipeline"}
                       else "Stopped. Finished chunks are saved; prepare the chapter again to resume." if job["kind"] == "listen_chapter"
                       else "Stopped. Finished audio is saved; prepare the performance again to resume." if job["kind"] == "performance"
                       else "Stopped. Completed takes are saved; generate again to resume.")
            self.store.update_job(job_id, status="interrupted" if self.stopping.is_set() else "cancelled", message=message)
        except Exception as exc:
            # Provider implementations sanitize their errors. Redact the key again at the boundary.
            message = str(exc) or type(exc).__name__
            for key in (*secrets, *self.api_keys.values(), *self.narration_keys.values()):
                if key:
                    message = message.replace(key, "[redacted]")
            message = message[:1200]
            self.store.update_job(job_id, status="failed", error=message,
                                  message="Stopped on an error. Validated chapter work is saved." if job["kind"] in {"analyze", "pipeline"} else "Stopped on an error. Completed takes are saved.")
        finally:
            CANCEL_CHECK.reset(cancel_token)
            if job['kind'] in {'listen', 'voice_preview'}:
                try:
                    settled = self.store.job(job_id)
                    state = settled['status']
                    if state in {'failed', 'cancelled', 'interrupted'}:
                        if job['kind'] == 'listen':
                            event = 'listen_job_failed' if state == 'failed' else 'listen_job_stopped'
                        else:
                            event = 'voice_preview_failed' if state == 'failed' else 'voice_preview_stopped'
                        record_safely(self.store, event,
                                      book_id=job['book_id'], segment_id=job.get('segment_id'),
                                      session_id=job.get('session_id'), job_id=job_id,
                                      provider=job.get('provider'), operation='worker', status=state)
                except Exception:
                    # Diagnostic/storage failure cannot replace the actual job
                    # result or turn a stopped request into another attempt.
                    pass

    def render(self, book_id, request):
        with self.store.lock:
            self.require_idle(book_id)
            book = self.store.book(book_id)
            if request.provider not in NARRATION_PROVIDERS:
                raise HTTPException(400, "Choose system, gemini or breeze narration")
            if request.provider == "gemini" and not self.api_key:
                raise HTTPException(400, "Add a Gemini API key in Settings first")
            if request.provider == "system" and not (shutil.which("say") and shutil.which("ffmpeg")):
                raise HTTPException(400, "Local narration requires macOS say and ffmpeg. Choose Gemini on other systems.")
            if request.provider == "breeze":
                self.require_breeze()
            selected = [s for s in book["segments"] if (not request.scene_id or s["scene_id"] == request.scene_id) and (not request.segment_id or s["id"] == request.segment_id)]
            if not selected:
                raise HTTPException(400, "No passages selected")
            # Snapshot the resolved voices: a version or default change while
            # this job runs must not mix voices within it.
            cast = self.resolved_cast(book)
            if request.provider in ("breeze", "gemini"):
                # Fail before queueing rather than part way through a scene.
                problems = {}
                for s in selected:
                    try:
                        voice_selection(cast[s["speaker_id"]], request.provider)
                    except AudioError as error:
                        problems.setdefault(cast[s["speaker_id"]].get("name", s["speaker_id"]), str(error))
                    except KeyError:
                        pass
                if problems:
                    names = sorted(problems)
                    more = "…" if len(names) > 5 else ""
                    raise HTTPException(400, f"Fix the {NARRATION_PROVIDERS[request.provider]['label'].split(' ·')[0]} voice for "
                                             f"{', '.join(names[:5])}{more}: {problems[names[0]]}")
            model = {"gemini": self.preferences["tts_model"], "breeze": BREEZE_MODEL}.get(request.provider, "macos-say")
            # Snapshot credentials and server configuration for this queued job.
            key = self.narration_credentials(request.provider)
            job = self.store.create_job(book_id, "render", len(selected))

            def work():
                characters = cast
                scenes = {s["id"]: s for s in book["scenes"]}
                lexicon = pronunciation.book_lexicon(book)
                reused = 0
                for i, s in enumerate(selected):
                    self.check_cancel(job["id"])
                    from .resources import ResourceLedger
                    with ResourceLedger(self.store).operation(book_id, "narration", run_id=job["id"], unit_key=s["id"],
                        chapter_id=s["chapter_id"], provider=request.provider, model=model, kind="narration") as metrics:
                        character, scene = characters[s["speaker_id"]], scenes[s["scene_id"]]
                        rendered = pronunciation.with_lexicon(s, lexicon)
                        fingerprint = render_fingerprint(rendered, character, scene, request.provider, model)
                        path = self.audio_path(book_id, fingerprint)
                        path.parent.mkdir(parents=True, exist_ok=True)
                        self.store.update_job(job["id"], message=f"Passage {i+1} of {len(selected)} · {character['name']}")
                        metadata = None
                        if not request.force:
                            current = s.get("audio")
                            if current and current.get("fingerprint") == fingerprint and self.valid_audio(book, s, cast):
                                try:
                                    duration = validate_audio(self.take_path(book_id, current))
                                    metadata = {**current, "duration": duration}
                                except (OSError, EOFError, ValueError):
                                    pass
                            if metadata is None:
                                # Editing a voice retires the selected take, but
                                # restoring its recipe can reuse archived WAV bytes.
                                with self.store.lock, self.store.connect() as conn:
                                    rows = conn.execute("""SELECT payload FROM artifact_versions
                                        WHERE book_id=? AND kind='audio_take' AND logical_key=?
                                        ORDER BY created_at DESC,rowid DESC""", (book_id, s['id'])).fetchall()
                                for (payload,) in rows:
                                    prior = json.loads(payload).get('audio', {})
                                    if prior.get('fingerprint') != fingerprint:
                                        continue
                                    try:
                                        duration = validate_audio(self.take_path(book_id, prior))
                                        metadata = {**prior, 'duration': duration}
                                        break
                                    except (OSError, EOFError, ValueError):
                                        continue
                            if metadata is None and path.is_file():
                                try:
                                    duration = validate_audio(path)
                                    metadata = {"fingerprint": fingerprint, "duration": duration, "provider": request.provider, "model": model,
                                                "voice": voice_id(character, request.provider)}
                                except (OSError, EOFError, ValueError):
                                    pass
                        if metadata is None:
                            metadata = produce_take(rendered, character, scene, request.provider, model, key, path.parent, synthesizer=synthesize)
                        else:
                            metrics["cached"] = True
                            reused += 1
                        s["audio"] = metadata
                        self.store.save_take(book_id, s["id"], metadata)
                        metrics.update(audio_seconds=metadata["duration"], output_bytes=self.take_path(book_id, metadata).stat().st_size)
                    self.store.update_job(job["id"], progress=i+1, message=f"Saved {i+1}/{len(selected)} passages · {reused} reused")

            self.pool.submit(self.run, job, work, self.narration_secrets(request.provider))
            return job

    def analyze(self, book_id, provider, chapter_id=None, resume=True, phase="scan", limits=None):
        with self.store.lock:
            self.require_idle(book_id)
            book = self.store.book(book_id)
            if chapter_id is not None and chapter_id not in {c["id"] for c in book["chapters"]}:
                raise HTTPException(400, "Choose a chapter in this book")
            provider = provider or self.preferences["analysis_provider"]
            if provider not in ANALYSIS_LABELS:
                raise HTTPException(400, "Choose local, gemini, openai, or anthropic analysis")
            key = self.api_keys.get(provider, "")
            if provider != "local" and not key:
                raise HTTPException(400, f"Add an {ANALYSIS_LABELS[provider]} API key in Settings first" if provider in {"openai", "anthropic"} else "Add a Gemini API key in Settings first")
            model = self.preferences["analysis_models_by_provider"].get(provider)
            scan_model = self.preferences["preprocess_models_by_provider"].get(provider)
            job = self.store.create_job(book_id, "analyze")
            job = self.store.update_job(job["id"], provider=provider, model=model, scan_model=scan_model, phase=phase, chapter_id=chapter_id)

            def work():
                def progress(done, total, message):
                    self.check_cancel(job["id"])
                    self.store.update_job(job["id"], progress=done, total=total, message=message)
                def prepare(snapshot):
                    self.check_cancel(job["id"])
                    self.assign_local_voices(snapshot)
                    cast = self.resolved_cast(snapshot)
                    for segment in snapshot["segments"]:
                        if segment.get("audio") and not self.valid_audio(snapshot, segment, cast):
                            segment["audio"] = None
                from contextlib import nullcontext
                from .resources import ResourceLedger
                timing = ResourceLedger(self.store).operation(book_id, 'local_analysis', run_id=job['id'],
                    chapter_id=chapter_id, measure_cpu=True) if provider == 'local' else nullcontext()
                with timing:
                    updated = analyze_book(book, provider, key, model, progress, lambda: self.cancelled(job["id"]),
                                           store=self.store, chapter_id=chapter_id, resume=resume, prepare=prepare,
                                           phase=phase, scan_model=scan_model, limits=limits, run_id=job["id"])
                self.check_cancel(job["id"])
                self.assign_local_voices(updated)
                updated["revision"] = book.get("revision", 0) + 1
                cast = self.resolved_cast(updated)
                for s in updated["segments"]:
                    if s.get("audio") and not self.valid_audio(updated, s, cast):
                        s["audio"] = None
                self.store.save_book(updated)
            self.pool.submit(self.run, job, work, (key,))
            return job


class UploadLimit:
    """Refuse an oversized book upload without reading the rest of it (413 `upload_too_large`).

    A declared Content-Length over the limit is refused before any body is read.
    Otherwise the body is counted as it streams, and reading stops at the limit.
    The route still checks the exact file size.
    """

    def __init__(self, app, limit: int):
        self.app, self.limit = app, limit

    async def refuse(self, send):
        body = json.dumps({"detail": "The upload is larger than 30 MiB.", "code": "upload_too_large"}).encode()
        await send({"type": "http.response.start", "status": 413,
                    "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())]})
        await send({"type": "http.response.body", "body": body})

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["method"] != "POST" or scope["path"] != "/api/books":
            return await self.app(scope, receive, send)
        declared = dict(scope["headers"]).get(b"content-length")
        if declared is not None and declared.strip().isdigit() and int(declared) > self.limit:
            return await self.refuse(send)
        state = {"received": 0, "exceeded": False, "started": False}

        async def limited_receive():
            if state["exceeded"]:
                return {"type": "http.disconnect"}
            message = await receive()
            if message["type"] == "http.request":
                state["received"] += len(message.get("body", b""))
                if state["received"] > self.limit:
                    state["exceeded"] = True
                    raise ValueError("upload exceeds the size limit")
            return message

        async def guarded_send(message):
            if state["exceeded"]:
                # Whatever the parser made of the aborted body, the answer is 413.
                if not state["started"]:
                    state["started"] = True
                    await self.refuse(send)
                return
            state["started"] = True
            await send(message)

        try:
            await self.app(scope, limited_receive, guarded_send)
        except Exception:
            if not state["exceeded"]:
                raise
            if not state["started"]:
                state["started"] = True
                await self.refuse(send)


def create_app(data_dir: Path | None = None):
    @asynccontextmanager
    async def lifespan(app):
        app.state.runtime = Runtime(data_dir if data_dir is not None else data_directory())
        yield
        app.state.runtime.close()

    app = FastAPI(title="Bardic", lifespan=lifespan, docs_url=None, redoc_url=None)
    from .importer import MAX_UPLOAD
    # Multipart framing adds a few hundred bytes around the file; allow 64 KiB.
    app.add_middleware(UploadLimit, limit=MAX_UPLOAD + 64 * 1024)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["localhost", "127.0.0.1", "[::1]", "testserver", *allowed_hosts()])

    @app.middleware("http")
    async def local_only(request: Request, call_next):
        origin = request.headers.get("origin")
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            if request.headers.get("sec-fetch-site") == "cross-site" or (origin and urlparse(origin).netloc != request.headers.get("host")):
                return JSONResponse({"detail": "Cross-origin writes are not allowed", "code": "cross_origin_write"}, status_code=403)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        if request.url.path.startswith("/api/") and "cache-control" not in response.headers:
            # A route that chooses its own caching (the content-addressed cover) keeps it.
            response.headers["Cache-Control"] = "no-store"
        return response

    # Every JSON error is {"detail": sentence, "code": stable code}; see bardic/errors.py.
    @app.exception_handler(ApiError)
    async def api_error(request, exc):
        return JSONResponse({"detail": exc.detail, "code": exc.code}, status_code=exc.status, headers=exc.headers)

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request, exc):
        code = STATUS_CODES.get(exc.status_code, "internal_error" if exc.status_code >= 500 else "invalid_request")
        return JSONResponse({"detail": exc.detail, "code": code}, status_code=exc.status_code, headers=exc.headers)

    @app.exception_handler(KeyError)
    async def missing(request, exc):
        # A resource the request names raises NotFound (an ApiError). A bare KeyError is a defect,
        # for example a dangling reference inside stored data, and must not look like "not found".
        return JSONResponse({"detail": "The server hit an unexpected error.", "code": "internal_error"}, status_code=500)

    @app.exception_handler(ValueError)
    async def invalid(request, exc):
        return JSONResponse({"detail": str(exc), "code": "invalid_request"}, status_code=400)

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request, exc):
        if request.url.path == '/api/diagnostics':
            # FastAPI's default validation body echoes invalid input. A
            # rejected accidental message/key must not enter this log API's
            # response either, even though it was never stored.
            return JSONResponse({'detail': 'Invalid diagnostic event fields.', 'code': 'validation_error'}, status_code=422)
        response = await request_validation_exception_handler(request, exc)
        body = json.loads(response.body)
        return JSONResponse({**body, 'code': 'validation_error'}, status_code=response.status_code)

    @app.exception_handler(Exception)
    async def unexpected(request, exc):
        return JSONResponse({"detail": "The server hit an unexpected error.", "code": "internal_error"}, status_code=500)

    def rt(request):
        return request.app.state.runtime

    @app.post('/api/diagnostics')
    def record_diagnostic(body: DiagnosticRequest, request: Request):
        fields = body.model_dump(exclude_none=True)
        event = fields.pop('event')
        return record_safely(rt(request).store, event, source='client', **fields)

    @app.get('/api/diagnostics')
    def diagnostics(request: Request, book_id: str | None = None, limit: int = 100):
        return DiagnosticRepository(rt(request).store).events(book_id=book_id, limit=limit)

    def status(runtime):
        voices = list_system_voices()
        with runtime.store.lock:
            preferences = copy.deepcopy(runtime.preferences)
            preferences.pop("breeze_catalog", None)
            preferences.pop("gemini_voice_catalog", None)
            breeze_view = runtime.breeze_view()
            breeze_ready = breeze_view["configured"] and any(voice["usable"] for voice in breeze_view["voices"])
            return {"has_api_key": bool(runtime.api_key), **preferences,
                    "providers": [{"id": "system", "label": "Mac voices · local", "available": bool(voices) and bool(shutil.which("ffmpeg"))},
                                  {"id": "gemini", "label": "Gemini · expressive", "available": bool(runtime.api_key)},
                                  {"id": "breeze", "label": NARRATION_PROVIDERS["breeze"]["label"], "available": breeze_ready,
                                   "reason": None if breeze_ready else breeze_view["message"]}],
                    "narration_providers": providers_status(), "breeze": breeze_view,
                    # Resolved (Settings, else environment); the saved-only values are not shown.
                    "local_service_urls": runtime.service_urls(),
                    "analysis_providers": [{"id": provider, "label": label,
                                            "available": provider == "local" or bool(runtime.api_keys.get(provider)),
                                            "has_api_key": bool(runtime.api_keys.get(provider)),
                                            "model": runtime.preferences["analysis_models_by_provider"].get(provider),
                                            "models": ANALYSIS_CATALOG.get(provider, [])}
                                           for provider, label in ANALYSIS_LABELS.items()],
                    "account_checks": {provider: runtime.account_check_view(provider) for provider in ANALYSIS_CATALOG},
                    "model_catalogs": {provider: runtime.model_catalog.view(provider, runtime.api_keys[provider]) for provider in ANALYSIS_CATALOG},
                    "system_voices": voices, "tts_models": TTS_MODELS, "analysis_models": ANALYSIS_MODELS,
                    "tts_rate": {model: LIMITER.view(model) for model in TTS_MODELS},
                    "data_directory": str(runtime.store.root), "timing_kind": "segment"}

    @app.get("/api/status")
    def get_status(request: Request):
        return status(rt(request))

    @app.post("/api/account-checks/{provider}")
    def check_provider_account(provider: str, request: Request):
        return rt(request).check_account(provider)

    @app.post("/api/narration/breeze/refresh")
    def refresh_breeze(request: Request):
        runtime = rt(request)
        view = runtime.refresh_breeze()
        if view["state"] == "ready" and runtime.voice_import_lock.acquire(blocking=False):
            try:
                import_breeze_voices(runtime, voice_api["set_default"])
            finally:
                runtime.voice_import_lock.release()
            with runtime.store.lock:
                view = runtime.breeze_view()
        return view

    @app.post("/api/models/{provider}/refresh")
    def refresh_provider_models(provider: str, request: Request):
        runtime = rt(request)
        if provider not in ANALYSIS_CATALOG:
            raise HTTPException(400, "Choose gemini, openai, or anthropic")
        with runtime.store.lock:
            key = runtime.api_keys[provider]
        try:
            result = runtime.model_catalog.refresh(provider, key)
        except Exception:
            # Unexpected transport exceptions can contain credentials. Keep the
            # documented choices usable without exposing the provider response.
            result = {**runtime.model_catalog.view(provider, key), "state": "unavailable",
                      "message": "The model list could not be refreshed. Try again later.", "cached": False}
        with runtime.store.lock:
            if key != runtime.api_keys[provider]:
                raise HTTPException(409, "The key changed during the refresh. Refresh the current key's models again.")
        return result

    @app.post("/api/settings")
    def settings(body: SettingsRequest, request: Request):
        runtime = rt(request)
        keys = dict(body.api_keys or {})
        models = dict(body.analysis_models_by_provider or {})
        preprocess_models = dict(body.preprocess_models_by_provider or {})
        if body.api_key is not None:
            if "gemini" in keys and keys["gemini"].strip() != body.api_key.strip():
                raise HTTPException(400, "Conflicting Gemini API key fields")
            keys["gemini"] = body.api_key
        if body.analysis_model is not None:
            if "gemini" in models and models["gemini"] != body.analysis_model:
                raise HTTPException(400, "Conflicting Gemini analysis model fields")
            models["gemini"] = body.analysis_model
        if (keys.keys() | models.keys() | preprocess_models.keys()) - ANALYSIS_CATALOG.keys():
            raise HTTPException(400, "Unknown cloud analysis provider")
        if any(not valid_analysis_model(model) for model in [*models.values(), *preprocess_models.values()]):
            raise HTTPException(400, "Use an analysis model ID with 1–200 letters, numbers, dots, underscores, colons, or hyphens")
        if body.analysis_provider is not None and body.analysis_provider not in ANALYSIS_LABELS:
            raise HTTPException(400, "Unknown analysis provider")
        if body.tts_model is not None and body.tts_model not in TTS_MODELS:
            raise HTTPException(400, "Unsupported tts_model")
        tts_limits = {}
        for model, value in (body.tts_limits or {}).items():
            if model not in TTS_MODELS:
                raise HTTPException(400, "Unsupported tts_model in rate limits")
            tts_limits[model] = normalize_limits(value)
        breeze_url = None
        if body.breeze_url is not None:
            try:
                breeze_url = breeze.normalize_base_url(body.breeze_url)
            except ValueError as error:
                raise HTTPException(400, str(error)) from None
        service_urls = {}
        for provider, value in (body.local_service_urls or {}).items():
            if provider not in local_services.SERVICES:
                raise HTTPException(400, "Unknown local analysis service")
            try:
                service_urls[provider] = local_services.normalize_url(value, provider)
            except ValueError as error:
                raise HTTPException(400, str(error)) from None
        chunking = None
        if body.listen_chunking is not None:
            chunking = normalize_options({**runtime.preferences["listen_chunking"],
                                          **body.listen_chunking.model_dump(exclude_none=True)})
        with runtime.store.lock:
            preferences = copy.deepcopy(runtime.preferences)
            preferences["tts_limits"].update(tts_limits)
            if chunking is not None:
                preferences["listen_chunking"] = chunking
            if breeze_url is not None:
                preferences["breeze_url"] = breeze_url
            preferences["local_service_urls"].update(service_urls)
            if body.tts_model is not None:
                preferences["tts_model"] = body.tts_model
            if body.analysis_provider is not None:
                preferences["analysis_provider"] = body.analysis_provider
            preferences["analysis_models_by_provider"].update(models)
            preferences["preprocess_models_by_provider"].update(preprocess_models)
            preferences["analysis_model"] = preferences["analysis_models_by_provider"]["gemini"]
            runtime.store.save_settings(preferences)
            runtime.preferences = preferences
            LIMITER.configure(preferences["tts_limits"])
            runtime.api_keys.update({provider: key.strip() for provider, key in keys.items()})
            if body.breeze_api_key is not None:
                runtime.narration_keys["breeze"] = body.breeze_api_key.strip()
            for provider, check in list(runtime.account_checks.items()):
                current = (runtime.api_keys[provider], preferences["analysis_models_by_provider"][provider])
                if check["configuration"] != current:
                    del runtime.account_checks[provider]
        return status(runtime)

    def library_repository(runtime):
        from .library import LibraryRepository
        return LibraryRepository(runtime.store, playable=runtime.playable_count)

    @app.get("/api/books")
    def list_books(request: Request):
        runtime = rt(request)
        repository = library_repository(runtime)
        return [repository.summary(book['id']) for book in runtime.store.books()]

    def discard_failed_import(store, book_id):
        """Remove what a failed import wrote, unless its book was already committed."""
        with store.lock, store.connect() as conn:
            if conn.execute("SELECT 1 FROM books WHERE id=?", (book_id,)).fetchone():
                return
            # The ID is new and unused, so these rows belong only to this failed import.
            conn.execute("DELETE FROM resource_operations WHERE book_id=?", (book_id,))
        shutil.rmtree(store.root / "originals" / book_id, ignore_errors=True)

    def persist_import(runtime, book, data=None, filename=None):
        runtime.assign_local_voices(book)
        if data is not None:
            path = runtime.store.root / "originals" / book["id"]
            path.mkdir(parents=True, exist_ok=True)
            suffix = Path(filename or "book.txt").suffix.lower()
            (path / f"source{suffix}").write_bytes(data)
        runtime.store.save_book(book)
        return runtime.present(book)

    @app.post("/api/books")
    async def import_book(request: Request, file: UploadFile = File(...)):
        from .errors import Invalid, TooLarge
        from .importer import MAX_UPLOAD
        data = await file.read(MAX_UPLOAD + 1)
        await file.close()
        if len(data) > MAX_UPLOAD:
            raise TooLarge("upload_too_large", "The upload is larger than 30 MiB.")
        from .resources import ResourceLedger
        runtime = rt(request)
        import_id = str(uuid4())
        try:
            with ResourceLedger(runtime.store).operation(import_id, 'import', measure_cpu=True) as metrics:
                try:
                    book = parse_book(file.filename or "book.txt", data)
                    book['id'] = import_id
                except (ValueError, zipfile.BadZipFile) as exc:
                    raise Invalid("book_file_invalid", str(exc)) from exc
                result = persist_import(runtime, book, data, file.filename)
                metrics['output_bytes'] = len(data)
                return result
        except BaseException:
            discard_failed_import(runtime.store, import_id)
            raise

    @app.post("/api/demo")
    def demo(request: Request):
        return persist_import(rt(request), analyze_book(make_demo_book(), "local"))

    @app.get("/api/books/{book_id}")
    def get_book(book_id: str, request: Request):
        return rt(request).present(rt(request).store.book(book_id))

    @app.post("/api/books/{book_id}/repair-structure")
    def repair_book_structure(book_id: str, request: Request):
        from .staged_analysis import fingerprint
        from .resources import ResourceLedger

        runtime = rt(request)
        with runtime.store.lock, ResourceLedger(runtime.store).operation(book_id, 'structure_repair', measure_cpu=True):
            runtime.require_idle(book_id)
            book = runtime.store.book(book_id)
            suffix = Path(book.get("source_name", "")).suffix.lower()
            if suffix not in {".epub", ".txt"}:
                raise HTTPException(400, "This book has no saved original EPUB or text file to refresh.")
            path = runtime.store.root / "originals" / book["id"] / f"source{suffix}"
            if not path.resolve().is_relative_to((runtime.store.root / "originals").resolve()) or not path.is_file():
                raise HTTPException(400, "The saved original is unavailable. Existing book work was preserved.")
            data = path.read_bytes()
            if len(data) > 30 * 1024 * 1024:
                raise HTTPException(400, "The saved original is too large to refresh. Existing book work was preserved.")
            try:
                updated = repair_structure(book, book["source_name"], data)
            except zipfile.BadZipFile as exc:
                raise HTTPException(400, "The saved original EPUB could not be read. Existing book work was preserved.") from exc
            updated["revision"] = book.get("revision", 0) + 1
            summary = runtime.store.analysis_status(book_id)
            checkpoint = runtime.store.analysis_checkpoint(book_id, summary["fingerprint"]) if summary else None
            if checkpoint:
                transformed = transform_checkpoint_structure(checkpoint, updated)
                new_fingerprint = fingerprint(updated, checkpoint["provider"], checkpoint.get("model"))
                runtime.store.commit_analysis(updated, new_fingerprint, transformed)
            else:
                runtime.store.save_book(updated)
            return runtime.present(updated)

    @app.get("/api/series")
    def list_series(request: Request):
        return SeriesRepository(rt(request).store).list_series()

    @app.post("/api/series")
    def create_series(body: SeriesNameRequest, request: Request):
        return SeriesRepository(rt(request).store).create_series(body.name)

    def require_series_not_running(runtime, series_id):
        from .errors import Conflict
        if series_id and any(job['status'] in ACTIVE for job in runtime.store.jobs('series:' + series_id)):
            raise Conflict('series_run_active', 'A run of this series is queued or running.')

    def book_series(runtime, book_id):
        repository = SeriesRepository(runtime.store)
        with runtime.store.lock:
            membership = repository.membership(book_id)
            selected = next((item for item in repository.list_series() if membership and item["id"] == membership["series_id"]), None)
            return {"membership": membership, "series": selected, "links": repository.links_for_book(book_id),
                    "characters": repository.list_characters(membership["series_id"]) if membership else []}

    @app.get("/api/books/{book_id}/series")
    def get_book_series(book_id: str, request: Request):
        return book_series(rt(request), book_id)

    @app.put("/api/books/{book_id}/series")
    def set_book_series(book_id: str, body: SeriesMembershipRequest, request: Request):
        runtime = rt(request)
        with runtime.store.lock:
            runtime.require_idle(book_id)
            require_series_not_running(runtime, body.series_id)
            SeriesRepository(runtime.store).set_membership(book_id, body.series_id, body.position)
            return book_series(runtime, book_id)

    @app.get("/api/series/{series_id}/characters")
    def get_series_characters(series_id: str, request: Request):
        return SeriesRepository(rt(request).store).list_characters(series_id)

    @app.post("/api/series/{series_id}/characters")
    def create_series_character(series_id: str, body: SeriesNameRequest, request: Request):
        return SeriesRepository(rt(request).store).create_character(series_id, body.name)

    @app.put("/api/books/{book_id}/series/characters/{character_id}")
    def link_series_character(book_id: str, character_id: str, body: SeriesCharacterLinkRequest, request: Request):
        runtime = rt(request)
        with runtime.store.lock:
            runtime.require_idle(book_id)
            repository = SeriesRepository(runtime.store)
            if body.series_character_id is None:
                return repository.unlink_character(book_id, character_id)
            return repository.link_character(book_id, character_id, body.series_character_id)

    @app.get("/api/books/{book_id}/series/context")
    def get_book_series_context(book_id: str, request: Request):
        return SeriesRepository(rt(request).store).context_for_book(book_id)

    @app.get("/api/books/{book_id}/analysis")
    def get_analysis(book_id: str, request: Request):
        runtime = rt(request)
        book = runtime.store.book(book_id)
        return runtime.store.analysis_status(book_id) or {
            "status": "not_started", "stage": "discovery", "provider": None, "model": None,
            "completed_units": 0, "total_units": 0, "current_chapter_id": None,
            "chapters": [{"id": c["id"], "title": c["title"], "stage": "discovery", "status": "pending",
                          "completed_units": 0, "total_units": 0, "discovery_complete": False, "directing_complete": False}
                         for c in book["chapters"]]}

    @app.get("/api/books/{book_id}/characters/{character_id}/references")
    def get_character_references(book_id: str, character_id: str, request: Request):
        runtime = rt(request)
        book = runtime.store.book(book_id)
        if character_id not in {c["id"] for c in book["characters"]}:
            raise HTTPException(404, "Character not found")
        return runtime.store.character_references(book_id, character_id)

    def edit(runtime, book_id, collection, item_id, fields):
        with runtime.store.lock:
            runtime.require_idle(book_id)
            book = runtime.store.book(book_id)
            item = next((x for x in book[collection] if x["id"] == item_id), None)
            if item is None:
                raise HTTPException(404, "Item not found")
            if "speaker_id" in fields and fields["speaker_id"] not in {c["id"] for c in book["characters"]}:
                raise HTTPException(400, "Choose a character in this book's cast")
            before = copy.deepcopy(item)
            if collection == "characters":
                runtime.merge_voices(item, fields)
            # Per-field edit locks, only for values that actually changed: editors
            # submit whole forms, so an unchanged description must not become locked.
            # An item edited before per-field tracking existed stays wholly locked.
            changed = {name for name, value in fields.items() if before.get(name) != value}
            if item.get("voices") != before.get("voices"):
                changed.add("voices")
            prior = item.get("edited_fields") if isinstance(item.get("edited_fields"), list) else (["*"] if item.get("edited") else [])
            item["edited_fields"] = sorted(set(prior) | changed)
            if collection == "characters" and fields.get("name") and fields["name"] != item.get("name"):
                # Remember replaced names so later discovery resolves them to this character.
                item["former_names"] = list(dict.fromkeys([*item.get("former_names", []), item["name"]]))
            item.update(fields)
            item["edited"] = True
            if collection == "segments" and "speaker_id" in fields:
                item["confidence"] = 1.0
                if "speaker_id" in changed:
                    # A BookNLP check judged the replaced speaker; the Quote attribution table still compares live.
                    item.pop("speaker_check", None)
            cast = runtime.resolved_cast(book)
            for s in book["segments"]:
                if s.get("audio") and not runtime.valid_audio(book, s, cast):
                    s["audio"] = None
            for scene in book["scenes"]:
                scene["character_ids"] = sorted({s["speaker_id"] for s in book["segments"] if s["scene_id"] == scene["id"]})
            book["revision"] = book.get("revision", 0) + 1
            runtime.store.save_book(book)
            return runtime.present(book)

    voice_api = register_voice_routes(app, rt, edit)

    def character_fields(body):
        fields = body.model_dump(exclude_none=True)
        if body.voices is not None:
            # A null entry removes that provider's choice, so keep it explicitly.
            fields["voices"] = {provider: choice.model_dump(exclude_none=True) if choice else None
                                for provider, choice in body.voices.items()}
        return fields

    @app.patch("/api/books/{book_id}/characters/{character_id}")
    def edit_character(book_id: str, character_id: str, body: CharacterEdit, request: Request):
        return edit(rt(request), book_id, "characters", character_id, character_fields(body))

    @app.post("/api/books/{book_id}/characters")
    def add_character(book_id: str, body: CharacterEdit, request: Request):
        runtime = rt(request)
        with runtime.store.lock:
            runtime.require_idle(book_id)
            book = runtime.store.book(book_id)
            if not body.name:
                raise HTTPException(400, "A character name is required")
            character = {"id": f"character-{uuid4().hex[:12]}", "name": body.name, "aliases": [], "description": "", "evidence": [],
                         "voices": {"gemini": {"id": "Kore"}}, "direction": ""}
            fields = character_fields(body)
            runtime.merge_voices(character, fields)
            character.update(fields)
            character["edited"] = True
            # The owner set only these fields; generated profile text may fill the rest.
            character["edited_fields"] = sorted(set(fields) | {"name"})
            book["characters"].append(character)
            book["revision"] = book.get("revision", 0) + 1
            runtime.store.save_book(book)
            return runtime.present(book)

    @app.patch("/api/books/{book_id}/segments/{segment_id}")
    def edit_segment(book_id: str, segment_id: str, body: SegmentEdit, request: Request):
        return edit(rt(request), book_id, "segments", segment_id, body.model_dump(exclude_none=True))

    @app.patch("/api/books/{book_id}/scenes/{scene_id}")
    def edit_scene(book_id: str, scene_id: str, body: SceneEdit, request: Request):
        return edit(rt(request), book_id, "scenes", scene_id, body.model_dump(exclude_none=True))

    # Pronunciations: respellings sent to narrators in place of a word. Book text never changes.

    def lexicon_usage(runtime, book):
        """Entries with their use in the book. Runs outside the store lock: it scans all text."""
        lexicon = pronunciation.book_lexicon(book)
        cast = runtime.resolved_cast(book)
        # Current Studio takes: an edit to an entry retires those containing its word (archived audio stays reusable).
        rendered = {s["id"] for s in book["segments"] if s.get("audio") and runtime.valid_audio(book, s, cast)}
        stats = pronunciation.usage(book["chapters"], book["segments"], lexicon, rendered=rendered)
        return [{**entry, "usage": stats[entry["id"]]} for entry in lexicon]

    def save_lexicon(runtime, book_id, change):
        with runtime.store.lock:
            runtime.require_idle(book_id)
            book = runtime.store.book(book_id)
            before = {entry["id"]: entry for entry in pronunciation.book_lexicon(book)}
            entries = pronunciation.normalize_lexicon(change(copy.deepcopy(list(before.values()))))
            characters = {c["id"] for c in book["characters"]}
            # Only the entry being changed must name a current character; an older link is left alone.
            if any(before.get(entry["id"]) != entry and entry.get("character_id") not in (None, *characters)
                   for entry in entries):
                raise HTTPException(400, "Choose a character in this book's cast")
            cast = runtime.resolved_cast(book)
            valid_before = {s["id"] for s in book["segments"] if s.get("audio") and runtime.valid_audio(book, s, cast)}
            if entries:
                book["pronunciations"] = entries
            else:
                book.pop("pronunciations", None)
            retired = 0
            for s in book["segments"]:
                if s["id"] in valid_before and not runtime.valid_audio(book, s, cast):
                    s["audio"] = None
                    retired += 1
            book["revision"] = book.get("revision", 0) + 1
            runtime.store.save_book(book)
            presented = runtime.present(book)
        return {"book": presented, "pronunciations": lexicon_usage(runtime, book), "retired_takes": retired}

    def lexicon_entry(body, entry_id=None):
        values = body.model_dump(exclude_none=True)
        values.pop("id", None)
        return pronunciation.normalize_entry(values, entry_id=entry_id)

    @app.get("/api/books/{book_id}/pronunciations")
    def list_pronunciations(book_id: str, request: Request):
        runtime = rt(request)
        book = runtime.store.book(book_id)
        return {"pronunciations": lexicon_usage(runtime, book)}

    @app.post("/api/books/{book_id}/pronunciations")
    def add_pronunciation(book_id: str, body: PronunciationEntry, request: Request):
        entry = lexicon_entry(body)
        return save_lexicon(rt(request), book_id, lambda entries: [*entries, entry])

    @app.patch("/api/books/{book_id}/pronunciations/{entry_id}")
    def edit_pronunciation(book_id: str, entry_id: str, body: PronunciationEntry, request: Request):
        # Fields left out keep their saved values; send null (or {} for providers) to clear one.
        changes = body.model_dump(exclude_unset=True)
        changes.pop("id", None)

        def change(entries):
            current = next((item for item in entries if item["id"] == entry_id), None)
            if current is None:
                raise HTTPException(404, "Pronunciation not found")
            values = {key: value for key, value in {**current, **changes}.items() if key != "id" and value is not None}
            entry = pronunciation.normalize_entry(values, entry_id=entry_id)
            return [entry if item["id"] == entry_id else item for item in entries]
        return save_lexicon(rt(request), book_id, change)

    @app.delete("/api/books/{book_id}/pronunciations/{entry_id}")
    def delete_pronunciation(book_id: str, entry_id: str, request: Request):
        def change(entries):
            if not any(item["id"] == entry_id for item in entries):
                raise HTTPException(404, "Pronunciation not found")
            return [item for item in entries if item["id"] != entry_id]
        return save_lexicon(rt(request), book_id, change)

    @app.post("/api/books/{book_id}/analyze")
    def analyze(book_id: str, body: AnalysisRequest, request: Request):
        return rt(request).analyze(book_id, body.provider, body.chapter_id, body.resume, body.phase, body.limits.model_dump())

    @app.get("/api/books/{book_id}/preprocessing")
    def preprocessing(book_id: str, request: Request):
        from .preprocessing import coverage
        from .progressive import discoveries, profile_status
        from .processing import ProcessingStore
        runtime = rt(request)
        with runtime.store.lock:
            book = runtime.store.book(book_id)
            accepted = discoveries(book, runtime.store, ProcessingStore(runtime.store))
            result = coverage(book, runtime.store)
            result.update(profile_status(book, runtime.store, accepted, result))
            return result

    @app.post("/api/books/{book_id}/analysis-plan")
    def analysis_plan(book_id: str, body: AnalysisRequest, request: Request):
        from .progressive import plan
        runtime = rt(request)
        with runtime.store.lock:
            book = runtime.store.book(book_id)
            if body.chapter_id and body.chapter_id not in {c['id'] for c in book['chapters']}:
                raise HTTPException(400, "Choose a chapter in this book")
            provider = body.provider or runtime.preferences['analysis_provider']
            if provider not in ANALYSIS_LABELS:
                raise HTTPException(400, "Choose a valid analysis provider")
            result = plan(book, runtime.store, provider, runtime.preferences['analysis_models_by_provider'].get(provider),
                          runtime.preferences['preprocess_models_by_provider'].get(provider), body.phase, body.chapter_id, body.resume)
            result['limits'] = body.limits.model_dump()
            return result

    @app.post("/api/books/{book_id}/render")
    def render(book_id: str, body: RenderRequest, request: Request):
        return rt(request).render(book_id, body)

    @app.get("/api/jobs")
    def jobs(request: Request, book_id: str | None = None, active: bool = False):
        # Every queued/running job, however many newer ones exist: `./bardicctl` checks this before stopping.
        return rt(request).store.jobs(book_id, limit=None if active else 100, active=active)

    @app.post("/api/jobs/{job_id}/cancel")
    def cancel(job_id: str, request: Request):
        runtime = rt(request)
        with runtime.store.lock:
            job = runtime.store.job(job_id)
            if job["status"] not in ACTIVE:
                return job
            if job['kind'] == 'series':
                for identifier in job.get('child_job_ids', []):
                    child = runtime.store.job(identifier)
                    if child['status'] == 'queued':
                        runtime.store.update_job(identifier, status='cancelled', cancel_requested=True,
                                                 message='Series cancelled before this book started.')
                    elif child['status'] == 'running':
                        runtime.store.update_job(identifier, cancel_requested=True, message='Stopping after current request.')
            if job['kind'] == 'performance':
                # A performance runs Gemini chapters as child jobs; stop the active one too.
                for child in runtime.store.jobs(job['book_id'], limit=None, active=True):
                    if child.get('parent_id') != job_id:
                        continue
                    if child['status'] == 'queued':
                        runtime.store.update_job(child['id'], status='cancelled', cancel_requested=True,
                                                 message='Performance cancelled before this chapter started.')
                    else:
                        runtime.store.update_job(child['id'], cancel_requested=True,
                                                 message='Stopping after the requests already sent. Their audio will be saved.')
            if job["status"] == "queued":
                return runtime.store.update_job(job_id, cancel_requested=True, status="cancelled", message="Cancelled before generation started.")
            return runtime.store.update_job(job_id, cancel_requested=True, message="Stopping after the current request. Finished takes will be kept.")

    @app.get("/api/audio/{book_id}/{segment_id}")
    def audio(book_id: str, segment_id: str, request: Request):
        runtime = rt(request)
        book = runtime.store.book(book_id)
        segment = next((s for s in book["segments"] if s["id"] == segment_id), None)
        if not segment or not runtime.valid_audio(book, segment):
            raise HTTPException(404, "This passage needs audio generation")
        return FileResponse(runtime.take_path(book_id, segment["audio"]), media_type="audio/wav")

    @app.get("/api/books/{book_id}/audio-assets/{asset_id}")
    def archived_audio(book_id: str, asset_id: str, request: Request):
        runtime = rt(request)
        runtime.store.book(book_id)
        try:
            path = runtime.audio_path(book_id, asset_id)
        except (ValueError, TypeError):
            raise HTTPException(404, "Audio asset not found") from None
        if not path.is_file():
            raise HTTPException(404, "Audio asset not found")
        return FileResponse(path, media_type="audio/wav")

    @app.get("/api/books/{book_id}/export")
    def export(book_id: str, request: Request):
        from .errors import Invalid
        runtime = rt(request)
        book = runtime.store.book(book_id)
        cast = runtime.resolved_cast(book)
        available = [s for s in book["segments"] if runtime.valid_audio(book, s, cast)]
        available_ids = {s["id"] for s in available}
        if not available:
            raise Invalid("export_audio_missing", "No passage has a current enhanced take to export.")
        temp = Path(tempfile.mkdtemp(prefix="bardic-export-"))
        try:
            export_path = temp / "audiobook.zip"
            manifest = {"title": book["title"], "timing_kind": "segment", "complete": len(available) == len(book["segments"]), "chapters": [], "missing_segment_ids": [s["id"] for s in book["segments"] if s["id"] not in available_ids]}
            # A GET records nothing: the export is a derived download, not a domain record.
            with zipfile.ZipFile(export_path, "w", zipfile.ZIP_DEFLATED) as archive:
                # The same presentation as GET /api/books/{book_id}, not the stored book.
                archive.writestr("production.json", json.dumps(runtime.present(book), ensure_ascii=False, indent=2))
                archive.writestr("README.txt", "Bardic audiobook export\nTimings identify exact audio passage boundaries, not words.\nOnly complete chapters are assembled. Individual completed takes are included even when a chapter is incomplete.\nSee timeline.json for missing passage IDs.\n")
                for s in available:
                    archive.write(runtime.take_path(book_id, s["audio"]), f"takes/{s['id']}.wav")
                for index, chapter in enumerate(book["chapters"]):
                    chapter_segments = [s for s in book["segments"] if s["chapter_id"] == chapter["id"]]
                    prefix = f"chapters/{index+1:03d}"
                    archive.writestr(f"{prefix}.txt", chapter["text"])
                    complete = all(s["id"] in available_ids for s in chapter_segments)
                    entry = {"id": chapter["id"], "title": chapter["title"], "complete": complete, "segments": []}
                    if chapter_segments and complete:
                        wav_path = temp / f"chapter-{index}.wav"
                        clips = [(s, runtime.take_path(book_id, s["audio"])) for s in chapter_segments]
                        try:
                            entry["segments"] = assemble_audio(clips, wav_path)
                        except AudioError as error:
                            raise Invalid("take_unreadable", str(error)) from error
                        entry["audio"] = f"{prefix}.wav"
                        archive.write(wav_path, entry["audio"])
                    manifest["chapters"].append(entry)
                archive.writestr("timeline.json", json.dumps(manifest, ensure_ascii=False, indent=2))
            name = re.sub(r"[^\w .-]", "", book["title"])[:80] or "audiobook"
            return FileResponse(export_path, media_type="application/zip", filename=f"{name}.zip", background=BackgroundTask(shutil.rmtree, temp))
        except Exception:
            shutil.rmtree(temp, ignore_errors=True)
            raise

    @app.get("/api/books/{book_id}/pipeline")
    def pipeline_inspector(book_id: str, request: Request):
        from .pipeline_view import pipeline
        runtime = rt(request)
        with runtime.store.lock:
            book = runtime.store.book(book_id)
            cast = runtime.resolved_cast(book)
            return pipeline(runtime.store, book, lambda book, segment: runtime.valid_audio(book, segment, cast))

    @app.get('/api/books/{book_id}/resources')
    def resource_usage(book_id: str, request: Request, limit: int = 100, offset: int = 0, run_id: str | None = None):
        from .resources import resource_summary
        store = rt(request).store
        store.book(book_id)
        return resource_summary(store, book_id, limit=limit, offset=offset, run_id=run_id)

    @app.get('/api/library')
    def library(request: Request, include_archived: bool = False):
        from .library import LibraryRepository
        return LibraryRepository(rt(request).store).snapshot(include_archived=include_archived)

    @app.patch('/api/books/{book_id}/metadata')
    def metadata(book_id: str, body: BookMetadataRequest, request: Request):
        runtime = rt(request)
        with runtime.store.lock:
            runtime.require_idle(book_id)
            return library_repository(runtime).update_book(book_id, title=body.title, author=body.author)

    @app.post('/api/books/{book_id}/archive')
    def archive_book(book_id: str, request: Request):
        from .library import LibraryRepository
        runtime = rt(request)
        with runtime.store.lock:
            runtime.store.book(book_id)
            if not runtime.store.is_archived(book_id):  # Archiving an archived book is a no-op.
                runtime.require_idle(book_id)
            return LibraryRepository(runtime.store).archive_book(book_id)

    @app.post('/api/books/{book_id}/restore')
    def restore_book(book_id: str, request: Request):
        from .library import LibraryRepository
        runtime = rt(request)
        with runtime.store.lock:
            runtime.store.book(book_id)
            if runtime.store.is_archived(book_id):  # Restoring an active book is a no-op.
                membership = SeriesRepository(runtime.store).membership(book_id, include_archived=True)
                require_series_not_running(runtime, membership['series_id'] if membership else None)
            return LibraryRepository(runtime.store).archive_book(book_id, archived=False)

    @app.post('/api/books/{book_id}/refresh-metadata')
    def refresh_book_metadata(book_id: str, request: Request):
        from .resources import ResourceLedger
        runtime = rt(request)
        with runtime.store.lock:
            # Refused requests (unknown, archived or busy book) record nothing; only real refresh work is measured.
            runtime.require_idle(book_id)
            with ResourceLedger(runtime.store).operation(book_id, 'metadata_refresh', measure_cpu=True):
                return library_repository(runtime).refresh_metadata(book_id)

    def etag_matches(header, etag):
        if header is None:
            return False
        if header.strip() == '*':
            return True
        # If-None-Match uses weak comparison: W/"x" matches "x".
        return any(tag.strip().removeprefix('W/') == etag for tag in header.split(','))

    @app.get('/api/books/{book_id}/cover')
    def book_cover(book_id: str, request: Request):
        from .library import LibraryRepository
        data, media_type, digest = LibraryRepository(rt(request).store).cover(book_id)
        etag = f'"{digest}"'
        # The summary's cover URL carries ?v={sha256}: that exact URL always names these bytes, so it
        # may be kept. Any other URL must be revalidated, which the strong ETag makes cheap.
        cache = 'private, max-age=31536000, immutable' if request.query_params.get('v') == digest else 'private, no-cache'
        headers = {'ETag': etag, 'Cache-Control': cache}
        if etag_matches(request.headers.get('if-none-match'), etag):
            return Response(status_code=304, headers=headers)
        return Response(data, media_type=media_type, headers=headers)

    def require_series_idle(runtime, series_id):
        from .series_processing import series_view
        series = series_view(runtime.store, series_id)
        for book in series['books']:
            runtime.require_idle(book['book_id'])
        require_series_not_running(runtime, series_id)
        return series

    @app.patch('/api/series/{series_id}')
    def rename_series(series_id: str, body: SeriesNameRequest, request: Request):
        from .library import LibraryRepository
        runtime = rt(request)
        with runtime.store.lock:
            require_series_idle(runtime, series_id)
            return LibraryRepository(runtime.store).rename_series(series_id, body.name)

    @app.post('/api/series/{series_id}/archive')
    def archive_series(series_id: str, request: Request):
        from .library import LibraryRepository
        runtime = rt(request)
        with runtime.store.lock:
            require_series_idle(runtime, series_id)
            return LibraryRepository(runtime.store).archive_series(series_id)

    @app.post('/api/series/{series_id}/restore')
    def restore_series(series_id: str, request: Request):
        from .library import LibraryRepository
        return LibraryRepository(rt(request).store).archive_series(series_id, archived=False)

    @app.put('/api/series/{series_id}/volumes')
    def add_series_volume(series_id: str, body: SeriesVolumeRequest, request: Request):
        from .library import LibraryRepository
        runtime = rt(request)
        with runtime.store.lock:
            require_series_idle(runtime, series_id)
            return LibraryRepository(runtime.store).add_volume(series_id, body.position, body.title, body.status)

    @app.delete('/api/series/{series_id}/volumes/{position}')
    def remove_series_volume(series_id: str, position: float, request: Request):
        from .library import LibraryRepository
        runtime = rt(request)
        with runtime.store.lock:
            require_series_idle(runtime, series_id)
            return LibraryRepository(runtime.store).remove_volume(series_id, position)

    @app.post('/api/series/{series_id}/plan')
    def plan_series(series_id: str, body: SeriesProcessingRequest, request: Request):
        from .series_processing import plan
        runtime = rt(request)
        with runtime.store.lock:
            return plan(runtime, series_id, provider=body.provider, phase=body.phase, concurrency=body.concurrency,
                        limits=body.limits.model_dump())

    @app.post('/api/series/{series_id}/process')
    def process_series(series_id: str, body: SeriesProcessingRequest, request: Request):
        from .series_processing import start
        return start(rt(request), series_id, provider=body.provider, phase=body.phase, concurrency=body.concurrency,
                     limits=body.limits.model_dump(), expected_plan_fingerprint=body.expected_plan_fingerprint)

    @app.get('/api/series/{series_id}/runs')
    def series_runs(series_id: str, request: Request):
        from .series_processing import series_view
        store = rt(request).store
        series_view(store, series_id)
        parents = store.jobs('series:' + series_id, limit=20)
        return {'runs': [{**parent, 'children': [store.job(identifier) for identifier in parent.get('child_job_ids', [])]} for parent in parents]}

    @app.get('/api/series/{series_id}/map')
    def series_map(series_id: str, request: Request):
        from .series_processing import series_view
        store = rt(request).store
        with store.lock:
            return {'series': series_view(store, series_id), 'characters': SeriesRepository(store).list_characters(series_id),
                    'note': 'Only confirmed identity links join characters across supplied titles. Absent volumes contribute no inferred evidence.'}

    @app.post('/api/books/{book_id}/listen')
    def simple_listen(book_id: str, body: ListenRequest, request: Request):
        from .listening import ListeningRepository
        from .resources import ResourceLedger
        runtime = rt(request)
        store = runtime.store
        repository = ListeningRepository(store)
        with store.lock:
            store.require_active(book_id)
            model = body.model or (runtime.preferences['tts_model'] if body.provider == 'gemini' else None)
            voice, selection = runtime.narrator_choice(body.provider, body.voice)
            session = repository.session(book_id, body.provider, voice, model, selection=selection)
            cached = repository.cached(book_id, session['id'], body.segment_id)
            if cached:
                with ResourceLedger(store).operation(book_id, 'simple_listen', unit_key=body.segment_id,
                                                    provider=body.provider, model=session['model'], cached=True, kind='narration') as metrics:
                    metrics['audio_seconds'] = cached['duration']
                return {'session': session, 'audio': cached, 'cached': True}
            # A lost HTTP response or overlapping playback/prefetch request
            # must join the existing work, not start another paid attempt.
            pending = next((job for job in store.jobs(book_id, limit=None)
                            if job['kind'] == 'listen' and job['status'] in ACTIVE
                            and not job.get('cancel_requested')
                            and job.get('session_id') == session['id']
                            and job.get('segment_id') == body.segment_id), None)
            if pending:
                return {'session': session, 'job': pending, 'cached': False}
            runtime.require_idle(book_id)
            if runtime.stopping.is_set():
                raise HTTPException(503, 'The local worker is stopping. Restart Bardic before preparing more audio.')
            key = runtime.narration_credentials(body.provider)
            if body.provider == 'gemini' and not key:
                raise HTTPException(400, 'Add a Gemini API key in Settings first, or choose a device voice.')
            if body.provider == 'system' and not (shutil.which('say') and shutil.which('ffmpeg')):
                raise HTTPException(400, 'Device narration requires macOS say and ffmpeg.')
            if body.provider == 'breeze':
                runtime.require_breeze()
            book = store.book(book_id)
            segment = next(s for s in book['segments'] if s['id'] == body.segment_id)
            job = store.create_job(book_id, 'listen', 1)
            job = store.update_job(job['id'], session_id=session['id'], segment_id=body.segment_id,
                                   provider=body.provider, model=session['model'], phase='simple_listen')
            def work():
                with ResourceLedger(store).operation(book_id, 'simple_listen', run_id=job['id'], unit_key=body.segment_id,
                    chapter_id=segment['chapter_id'], provider=body.provider, model=session['model'], kind='narration') as metrics:
                    audio = repository.render_passage(book_id, session['id'], body.segment_id, key, synthesizer=synthesize,
                                                       check_cancel=lambda: runtime.check_cancel(job['id']))
                    metrics.update(audio_seconds=audio['duration'], output_bytes=repository.asset_path(book_id, audio['asset_id']).stat().st_size)
                    if audio.get('cache_hit'):
                        metrics['cached'] = True
                store.update_job(job['id'], progress=1, audio=audio)
            try:
                future = runtime.pool.submit(runtime.run, job, work, runtime.narration_secrets(body.provider))
            except RuntimeError:
                store.update_job(job['id'], status='failed', error='The local narration worker could not accept this request.',
                                 message='No narration was started. Restart Bardic and try again.')
                record_safely(store, 'listen_submit_failed', book_id=book_id, segment_id=body.segment_id,
                              session_id=session['id'], job_id=job['id'], provider=body.provider,
                              operation='submit', status='failed')
                raise HTTPException(503, 'The local narration worker could not accept this request. No narration was started.') from None
            def settle_cancelled(future):
                if future.cancelled():
                    state = 'interrupted' if runtime.stopping.is_set() else 'cancelled'
                    store.update_job(job['id'], status=state,
                                     cancel_requested=True, message='Stopped before narration started. No provider request was sent.')
                    record_safely(store, 'listen_job_stopped', book_id=book_id, segment_id=body.segment_id,
                                  session_id=session['id'], job_id=job['id'], provider=body.provider,
                                  operation='worker', status=state)
            future.add_done_callback(settle_cancelled)
            return {'session': session, 'job': job, 'cached': False}

    @app.get('/api/books/{book_id}/listen/takes')
    def listen_takes(book_id: str, session_id: str, request: Request):
        from .listening import ListeningRepository
        return ListeningRepository(rt(request).store).takes(book_id, session_id)

    @app.get('/api/books/{book_id}/listen/audio/{asset_id}')
    def listen_audio(book_id: str, asset_id: str, request: Request):
        from .listening import ListeningRepository
        return FileResponse(ListeningRepository(rt(request).store).asset_path(book_id, asset_id), media_type='audio/wav')

    def chapter_listen_context(runtime, book_id, body):
        """Resolve session, chapter and chunk options for a chapter request. Local only."""
        from .listening import ListeningRepository
        store = runtime.store
        repository = ListeningRepository(store)
        store.require_active(book_id)
        model = body.model or runtime.preferences['tts_model']
        voice, _ = runtime.narrator_choice(body.provider, body.voice)
        session = repository.session(book_id, body.provider, voice, model)
        book = store.book(book_id)
        segment = next((s for s in book['segments'] if s['id'] == body.segment_id), None)
        if segment is None:
            raise NotFound('passage_not_found', 'Passage not found in this book')
        chapter, segments = repository.chapter_segments(book, segment['chapter_id'])
        chosen = {**runtime.preferences['listen_chunking'], **(body.chunking.model_dump(exclude_none=True) if body.chunking else {})}
        if body.intent == 'queue' and not (body.chunking and body.chunking.ramp_seconds is not None):
            # Queued work does not need a quick first clip; every request is full size.
            chosen['ramp_seconds'] = []
        options = normalize_options(chosen)
        takes = repository.takes(book_id, session['id'])['takes']
        ready = {take['segment_id']: take['audio'] for take in takes}
        blocked = {segment_id for segment_id, audio in ready.items() if audio.get('chunk_id')}
        previous = next((job for job in store.jobs(book_id, limit=None)
                         if job['kind'] == 'listen_chapter' and job.get('session_id') == session['id'] and job.get('calibration')), None)
        calibration = Calibration(previous.get('calibration') if previous else None)
        return repository, session, chapter, segments, segment, options, ready, blocked, calibration

    def chapter_summary(runtime, model, segments, chapter, segment, options, ready, blocked, calibration):
        position = next(i for i, s in enumerate(segments) if s['id'] == segment['id'])
        chunks = plan_chunks(segments, chapter['text'], scope_start=position, focus=position, blocked=blocked,
                             covered=set(ready), options=options, calibration=calibration)
        scope = segments[position:]
        limits = runtime.preferences['tts_limits'].get(model, dict(DEFAULT_TTS_LIMITS))
        return {'chunks': [{'first_segment_id': c['segment_ids'][0], 'last_segment_id': c['segment_ids'][-1],
                            'segment_count': len(c['segment_ids']), 'chars': c['chars'],
                            'target_seconds': c['target_seconds'], 'expected_seconds': c['expected_seconds']} for c in chunks],
                'requests_needed': len(chunks), 'expected_seconds': round(sum(c['expected_seconds'] for c in chunks), 1),
                'ready_seconds': round(sum(ready[s['id']]['duration'] for s in scope if s['id'] in ready), 1),
                'passages_total': len(scope), 'passages_ready': sum(1 for s in scope if s['id'] in ready),
                'chunking': options, 'calibration': calibration.view(), 'limits': limits,
                'quota': {'requests_today': requests_today(runtime.store, model), 'rpd': limits['rpd'],
                          'resets_at': quota_day()[1].isoformat(), 'scope': 'this library'}}

    @app.post('/api/books/{book_id}/listen/chapter/preview')
    def preview_chapter_listen(book_id: str, body: ChapterListenRequest, request: Request):
        runtime = rt(request)
        with runtime.store.lock:
            _, session, chapter, segments, segment, options, ready, blocked, calibration = chapter_listen_context(runtime, book_id, body)
        # Planning and the ledger scan are local reads; keep them outside the global lock.
        return {'session': session, 'chapter_id': chapter['id'],
                **chapter_summary(runtime, session['model'], segments, chapter, segment, options, ready, blocked, calibration)}

    @app.post('/api/books/{book_id}/listen/chapter')
    def start_chapter_listen(book_id: str, body: ChapterListenRequest, request: Request):
        runtime = rt(request)
        store = runtime.store
        with store.lock:
            _, session, chapter, segments, segment, options, ready, _, calibration = chapter_listen_context(runtime, book_id, body)
            position = {s['id']: i for i, s in enumerate(segments)}
            active = next((job for job in store.jobs(book_id, limit=None)
                           if job['kind'] == 'listen_chapter' and job['status'] in ACTIVE and not job.get('cancel_requested')), None)
            if active:
                if active.get('parent_id'):
                    # Joining would let live listening stop the performance's job.
                    raise HTTPException(409, 'A saved performance is preparing this book. Play that performance, or wait for it to finish.')
                if active.get('session_id') != session['id'] or active.get('chapter_id') != chapter['id']:
                    raise HTTPException(409, 'Another chapter or narrator is being prepared. Stop it before starting this one.')
                if active.get('closing'):
                    raise HTTPException(409, 'The chapter job is finishing. Try again in a moment.')
                # Joining moves generation to the listener; it never starts a second job.
                fields = {'focus_segment_id': segment['id'], 'joins': active.get('joins', 0) + 1}
                if position[segment['id']] < position.get(active.get('scope_start_segment_id'), 0):
                    fields['scope_start_segment_id'] = segment['id']
                in_flight = any(entry.get('status') == 'requesting' and
                                position.get(entry['first_segment_id'], -1) <= position[segment['id']] <= position.get(entry['last_segment_id'], -1)
                                for entry in active.get('chunks', []))
                if body.intent == 'play' and segment['id'] not in ready and not in_flight:
                    fields['ramp_restart'] = active.get('ramp_restart', 0) + 1
                return {'session': session, 'job': store.update_job(active['id'], **fields), 'joined': True}
            runtime.require_idle(book_id)
            if runtime.stopping.is_set():
                raise HTTPException(503, 'The local worker is stopping. Restart Bardic before preparing more audio.')
            key = runtime.api_key
            if not key:
                raise HTTPException(400, 'Add a Gemini API key in Settings first, or choose a device voice.')
            limits = runtime.preferences['tts_limits'].get(session['model'], dict(DEFAULT_TTS_LIMITS))
            blocked_for = LIMITER.daily_block(session['model'])
            if blocked_for > 0:
                # A provider daily-quota rejection holds until the Pacific reset
                # (or until the limits are saved again in Settings).
                raise HTTPException(429, f'The daily Gemini request quota for this model is used up. It resets at midnight Pacific time, in about {max(1, round(blocked_for / 3600))} h.')
            job = store.create_job(book_id, 'listen_chapter', len(segments) - position[segment['id']])
            job = store.update_job(job['id'], session_id=session['id'], chapter_id=chapter['id'], provider='gemini',
                                   model=session['model'], voice=session['voice'], intent=body.intent,
                                   scope_start_segment_id=segment['id'], focus_segment_id=segment['id'],
                                   chunking=options, limits=limits, ramp_restart=0, joins=0, phase='chapter_listen', chunks=[],
                                   calibration=calibration.view())
            coordinator = ChapterCoordinator(store, job['id'], key, runtime.narration_pool,
                                             cancelled=lambda: runtime.cancelled(job['id']))
            try:
                future = runtime.listen_pool.submit(runtime.run, job, coordinator.run, (key,))
            except RuntimeError:
                store.update_job(job['id'], status='failed', error='The local narration worker could not accept this request.',
                                 message='No narration was started. Restart Bardic and try again.')
                raise HTTPException(503, 'The local narration worker could not accept this request. No narration was started.') from None

            def settle_cancelled(future):
                if future.cancelled():
                    store.update_job(job['id'], status='interrupted' if runtime.stopping.is_set() else 'cancelled',
                                     cancel_requested=True, message='Stopped before any chunk was requested.')
            future.add_done_callback(settle_cancelled)
            return {'session': session, 'job': job, 'joined': False}

    # Saved performances -------------------------------------------------

    def performance_request(body):
        return body.model_dump()

    @app.get('/api/books/{book_id}/performances')
    def list_performances(book_id: str, request: Request, archived: bool = False):
        from . import performances
        runtime = rt(request)
        book = runtime.store.book(book_id)
        records = performances.PerformanceRepository(runtime.store).list(book_id, include_archived=archived)
        return {'performances': [performances.present(runtime, record, book) for record in records]}

    @app.post('/api/books/{book_id}/performances/preview')
    def preview_performance(book_id: str, body: PerformanceRequest, request: Request):
        from . import performances
        runtime = rt(request)
        runtime.store.require_active(book_id)
        return performances.plan(runtime, book_id, performance_request(body))['public']

    @app.post('/api/books/{book_id}/performances')
    def create_performance(book_id: str, body: PerformanceRequest, request: Request):
        from . import performances
        runtime = rt(request)
        with runtime.store.lock:
            return performances.create(runtime, book_id, performance_request(body))

    @app.get('/api/books/{book_id}/performances/{performance_id}')
    def get_performance(book_id: str, performance_id: str, request: Request):
        from . import performances
        runtime = rt(request)
        record = performances.PerformanceRepository(runtime.store).get(book_id, performance_id)
        return {'performance': performances.present(runtime, record)}

    @app.get('/api/books/{book_id}/performances/{performance_id}/audio')
    def performance_audio(book_id: str, performance_id: str, request: Request):
        from . import performances
        runtime = rt(request)
        record = performances.PerformanceRepository(runtime.store).get(book_id, performance_id)
        return {'performance_id': performance_id, 'audio': performances.ready_audio(runtime, record)}

    @app.post('/api/books/{book_id}/performances/{performance_id}/prepare')
    def prepare_performance(book_id: str, performance_id: str, request: Request):
        from . import performances
        runtime = rt(request)
        with runtime.store.lock:
            return performances.prepare(runtime, book_id, performance_id)

    @app.patch('/api/books/{book_id}/performances/{performance_id}')
    def edit_performance(book_id: str, performance_id: str, body: PerformanceEdit, request: Request):
        from . import performances
        runtime = rt(request)
        repository = performances.PerformanceRepository(runtime.store)
        repository.get(book_id, performance_id)
        fields = body.model_dump(exclude_none=True)
        if 'name' in fields:
            fields['name'] = fields['name'].strip()
            if not fields['name']:
                raise HTTPException(400, 'A performance name is required.')
        record = repository.update(book_id, performance_id, **fields) if fields else repository.get(book_id, performance_id)
        return {'performance': performances.present(runtime, record)}

    @app.post('/api/books/{book_id}/voice-preview')
    def voice_preview(book_id: str, body: VoicePreviewRequest, request: Request):
        from .voice_previews import VoicePreviewRepository
        from .resources import ResourceLedger
        runtime = rt(request)
        store = runtime.store
        repository = VoicePreviewRepository(store)
        with store.lock:
            store.require_active(book_id)
            model = body.model or (runtime.preferences['tts_model'] if body.provider == 'gemini' else None)
            voice, selection = runtime.narrator_choice(body.provider, body.voice)
            preview = repository.prepare(book_id, body.provider, voice, model,
                                         segment_id=body.segment_id, character_id=body.character_id,
                                         direction=body.direction, segment_direction=body.segment_direction,
                                         selection=selection,
                                         pronunciation_draft=body.pronunciation.model_dump(exclude_none=True)
                                         if body.pronunciation else None)
            cached = repository.cached(book_id, preview['id'])
            if cached:
                with ResourceLedger(store).operation(book_id, 'voice_preview', unit_key=preview['id'],
                        chapter_id=preview['chapter_id'], provider=body.provider, model=preview['model'],
                        cached=True, kind='narration') as metrics:
                    metrics['audio_seconds'] = cached['duration']
                return {'preview': preview, 'audio': cached, 'cached': True}
            pending = next((job for job in store.jobs(book_id, limit=None)
                            if job['kind'] == 'voice_preview' and job['status'] in ACTIVE
                            and not job.get('cancel_requested') and job.get('preview_id') == preview['id']), None)
            if pending:
                return {'preview': preview, 'job': pending, 'cached': False}
            runtime.require_idle(book_id)
            if runtime.stopping.is_set():
                raise HTTPException(503, 'The local worker is stopping. Restart Bardic before previewing a voice.')
            key = runtime.narration_credentials(body.provider)
            if body.provider == 'gemini' and not key:
                raise HTTPException(400, 'Add a Gemini API key in Settings first, or choose a device voice.')
            if body.provider == 'system' and not (shutil.which('say') and shutil.which('ffmpeg')):
                raise HTTPException(400, 'Device narration requires macOS say and ffmpeg.')
            if body.provider == 'breeze':
                runtime.require_breeze()
            job = store.create_job(book_id, 'voice_preview', 1)
            job = store.update_job(job['id'], preview_id=preview['id'], preview=preview,
                                   segment_id=preview['segment_id'], provider=body.provider,
                                   model=preview['model'], phase='voice_preview')
            def work():
                with ResourceLedger(store).operation(book_id, 'voice_preview', run_id=job['id'], unit_key=preview['id'],
                        chapter_id=preview['chapter_id'], provider=body.provider, model=preview['model'], kind='narration') as metrics:
                    audio = repository.render(book_id, preview['id'], key, synthesizer=synthesize,
                                              check_cancel=lambda: runtime.check_cancel(job['id']))
                    metrics.update(audio_seconds=audio['duration'], output_bytes=repository.asset_path(book_id, audio['asset_id']).stat().st_size)
                    if audio.get('cache_hit'):
                        metrics['cached'] = True
                store.update_job(job['id'], progress=1, audio=audio)
            try:
                future = runtime.pool.submit(runtime.run, job, work, runtime.narration_secrets(body.provider))
            except RuntimeError:
                store.update_job(job['id'], status='failed', error='The local narration worker could not accept this request.',
                                 message='No narration was started. Restart Bardic and try again.')
                record_safely(store, 'voice_preview_submit_failed', book_id=book_id,
                              segment_id=preview['segment_id'], job_id=job['id'],
                              provider=body.provider, operation='submit', status='failed')
                raise HTTPException(503, 'The local narration worker could not accept this request. No narration was started.') from None
            def settle_cancelled(future):
                if future.cancelled():
                    state = 'interrupted' if runtime.stopping.is_set() else 'cancelled'
                    store.update_job(job['id'], status=state,
                                     cancel_requested=True, message='Stopped before narration started. No provider request was sent.')
                    record_safely(store, 'voice_preview_stopped', book_id=book_id,
                                  segment_id=preview['segment_id'], job_id=job['id'],
                                  provider=body.provider, operation='worker', status=state)
            future.add_done_callback(settle_cancelled)
            return {'preview': preview, 'job': job, 'cached': False}

    @app.get('/api/books/{book_id}/voice-preview/audio/{asset_id}')
    def voice_preview_audio(book_id: str, asset_id: str, request: Request):
        from .voice_previews import VoicePreviewRepository
        return FileResponse(VoicePreviewRepository(rt(request).store).asset_path(book_id, asset_id), media_type='audio/wav')

    @app.get("/api/books/{book_id}/artifacts")
    def artifacts(book_id: str, request: Request, kind: str | None = None, stage: str | None = None,
                  current: bool | None = None, limit: int = 30, offset: int = 0):
        from .pipeline_view import prepare
        store = rt(request).store
        store.book(book_id)
        return prepare(store, book_id).list(book_id, kind=kind, stage=stage, current=current, limit=limit, offset=offset)

    @app.get("/api/books/{book_id}/artifacts/{artifact_id}")
    def artifact(book_id: str, artifact_id: str, request: Request):
        from .pipeline_view import prepare
        store = rt(request).store
        store.book(book_id)
        return prepare(store, book_id).get(book_id, artifact_id)

    @app.get("/api/books/{book_id}/story-map")
    def book_story_map(book_id: str, request: Request):
        from .pipeline_view import story_map
        store = rt(request).store
        with store.lock:
            return story_map(store, store.book(book_id))

    @app.get("/api/books/{book_id}/search")
    def passage_search(book_id: str, request: Request, q: str, scope: str = 'book', limit: int = 20):
        from .search import search
        from .resources import ResourceLedger
        store = rt(request).store
        store.book(book_id)
        with ResourceLedger(store).operation(book_id, 'source_search', measure_cpu=True):
            result = search(store, book_id, q, scope=scope, limit=limit)
        return {**result, 'results': result['items'], 'query': q, 'scope': scope}

    @app.get("/api/books/{book_id}/analysis-export")
    def analysis_export(book_id: str, request: Request):
        from .pipeline_view import write_analysis_export
        from .resources import ResourceLedger
        store = rt(request).store
        book = store.book(book_id)
        temp = Path(tempfile.mkdtemp(prefix='bardic-analysis-'))
        try:
            path = temp / 'analysis.zip'
            with ResourceLedger(store).operation(book_id, 'analysis_export', measure_cpu=True) as metrics:
                write_analysis_export(store, book_id, path)
                metrics['output_bytes'] = path.stat().st_size
            name = re.sub(r'[^\w .-]', '', book['title'])[:80] or 'book'
            return FileResponse(path, media_type='application/zip', filename=f'{name}-analysis.zip',
                                background=BackgroundTask(shutil.rmtree, temp))
        except Exception:
            shutil.rmtree(temp, ignore_errors=True)
            raise

    app.include_router(pipeline_router(default_registry()))
    install_contract(app)

    app.mount("/static", StaticFiles(directory=STATIC), name="assets")
    app.mount("/", StaticFiles(directory=STATIC, html=True), name="studio")
    return app


app = create_app()
