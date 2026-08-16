from __future__ import annotations

from .cache import AnalysisCache, make_context_hash
from .context_builder import ContextBuilder
from .models import AIAnalysisRequest, AnalysisCandidate, AnalysisExecution, AnalysisStatus
from .prompts import SYSTEM_PROMPT, build_repair_prompt, build_user_prompt
from .providers.base import AIProvider, ProviderError, ProviderTimeout, ProviderUnavailable
from .validator import ValidationError, parse_and_validate_result

PROMPT_VERSION = "a5-v3-assertion-evidence"


class AIAnalyzer:
    def __init__(self, *, context_builder: ContextBuilder, provider: AIProvider, cache: AnalysisCache | None = None) -> None:
        self.context_builder = context_builder
        self.provider = provider
        self.cache = cache

    def analyze(self, request: AIAnalysisRequest, candidate: AnalysisCandidate | None = None) -> AnalysisExecution:
        context = self.context_builder.build(request, candidate)
        context_hash = make_context_hash(
            context_payload=context.prompt_payload(),
            analysis_type=request.analysis_type.value,
            mode=request.mode.value,
            provider_name=self.provider.provider_name,
            model_name=self.provider.model_name,
            prompt_version=PROMPT_VERSION,
        )
        if self.cache and not request.force_refresh:
            cached = self.cache.get(context_hash)
            if cached is not None:
                return AnalysisExecution(AnalysisStatus.CACHE_HIT, cached, context_hash)
        user_prompt = build_user_prompt(context, request.user_question)
        try:
            raw = self.provider.analyze(system_prompt=SYSTEM_PROMPT, user_prompt=user_prompt)
        except ProviderTimeout as exc:
            self._store_failure(context_hash, request, AnalysisStatus.ANALYSIS_TIMEOUT, str(exc))
            return AnalysisExecution(AnalysisStatus.ANALYSIS_TIMEOUT, None, context_hash, str(exc))
        except ProviderUnavailable as exc:
            self._store_failure(context_hash, request, AnalysisStatus.MODEL_UNAVAILABLE, str(exc))
            return AnalysisExecution(AnalysisStatus.MODEL_UNAVAILABLE, None, context_hash, str(exc))
        except ProviderError as exc:
            self._store_failure(context_hash, request, AnalysisStatus.INVALID_OUTPUT, str(exc))
            return AnalysisExecution(AnalysisStatus.INVALID_OUTPUT, None, context_hash, str(exc))
        try:
            result = parse_and_validate_result(raw, context)
        except ValidationError as first_error:
            repair_prompt = build_repair_prompt(user_prompt, raw, str(first_error))
            try:
                repaired_raw = self.provider.analyze(system_prompt=SYSTEM_PROMPT, user_prompt=repair_prompt)
            except ProviderTimeout as exc:
                self._store_failure(context_hash, request, AnalysisStatus.ANALYSIS_TIMEOUT, str(exc))
                return AnalysisExecution(AnalysisStatus.ANALYSIS_TIMEOUT, None, context_hash, str(exc))
            except ProviderUnavailable as exc:
                self._store_failure(context_hash, request, AnalysisStatus.MODEL_UNAVAILABLE, str(exc))
                return AnalysisExecution(AnalysisStatus.MODEL_UNAVAILABLE, None, context_hash, str(exc))
            except ProviderError as exc:
                self._store_failure(context_hash, request, AnalysisStatus.INVALID_OUTPUT, str(exc))
                return AnalysisExecution(AnalysisStatus.INVALID_OUTPUT, None, context_hash, str(exc))
            try:
                result = parse_and_validate_result(repaired_raw, context)
            except ValidationError as second_error:
                error = f"initial validation: {first_error}; repair validation: {second_error}"
                self._store_failure(context_hash, request, AnalysisStatus.FAILED_VALIDATION, error)
                return AnalysisExecution(AnalysisStatus.FAILED_VALIDATION, None, context_hash, error)
        if self.cache:
            self.cache.put(
                context_hash=context_hash,
                conversation_id=request.conversation_id,
                analysis_type=request.analysis_type.value,
                mode=request.mode.value,
                provider_name=self.provider.provider_name,
                model_name=self.provider.model_name,
                prompt_version=PROMPT_VERSION,
                status=AnalysisStatus.COMPLETED,
                result=result,
            )
        return AnalysisExecution(AnalysisStatus.COMPLETED, result, context_hash)

    def _store_failure(self, context_hash: str, request: AIAnalysisRequest, status: AnalysisStatus, error: str) -> None:
        if not self.cache:
            return
        self.cache.put(
            context_hash=context_hash,
            conversation_id=request.conversation_id,
            analysis_type=request.analysis_type.value,
            mode=request.mode.value,
            provider_name=self.provider.provider_name,
            model_name=self.provider.model_name,
            prompt_version=PROMPT_VERSION,
            status=status,
            result=None,
            error=error,
        )
