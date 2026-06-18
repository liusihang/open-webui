from __future__ import annotations

import logging
import re
from collections.abc import Awaitable, Callable
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from open_webui.models.agent_runs import AgentRuns
from open_webui.models.users import Users
from open_webui.utils.models import check_model_access, get_all_models

log = logging.getLogger(__name__)


class ModelCatalogError(ValueError):
    code = 'model_catalog_error'


class ModelCatalogRunRejected(ModelCatalogError):
    code = 'model_catalog_run_rejected'


class ModelSelectionNotAllowed(ModelCatalogError):
    code = 'model_selection_not_allowed'

    def __init__(self, message: str, *, warnings: list[dict[str, Any]] | None = None) -> None:
        super().__init__(message)
        self.warnings = warnings or []


class ModelSelectionRequest(BaseModel):
    model_config = ConfigDict(extra='allow')

    run_id: str
    participant_id: str
    selection_id: str
    requested_model_id: str | None = None
    fuzzy_request: str | None = None
    source_request: dict[str, Any] = Field(default_factory=dict)
    idempotency_key: str | None = None


UserLoader = Callable[[str], Awaitable[Any]]
ModelLoader = Callable[[Any, Any], Awaitable[Any]]
ModelAccessChecker = Callable[[Any, dict[str, Any]], Awaitable[None]]


class AgentModelCatalog:
    def __init__(
        self,
        *,
        run_store=AgentRuns,
        user_loader: UserLoader = Users.get_user_by_id,
        model_loader: ModelLoader | None = None,
        model_access_checker: ModelAccessChecker = check_model_access,
    ) -> None:
        self.run_store = run_store
        self.user_loader = user_loader
        self.model_loader = model_loader or _load_all_models
        self.model_access_checker = model_access_checker

    async def select_model(
        self,
        request,
        selection: ModelSelectionRequest,
    ) -> dict[str, Any]:
        run = await self.run_store.get_run(selection.run_id)
        if run is None:
            raise ModelCatalogRunRejected(f'Agent run not found: {selection.run_id}')
        if run.state != 'running':
            raise ModelCatalogRunRejected(
                f'Agent run {selection.run_id} cannot select models while {run.state}',
            )

        user = await self.user_loader(run.user_id)
        if user is None:
            raise ModelCatalogRunRejected(f'Agent run user not found: {run.user_id}')

        allowed_choices = await self._allowed_model_choices(request, user)
        selected, reason = self._select_allowed_choice(allowed_choices, selection)
        source_request = _selection_source_request(selection)

        return {
            'choices': allowed_choices,
            'selected_model_id': selected['id'],
            'meta': {
                'agent_selection': {
                    'reason': reason,
                    'source_request': source_request,
                    'selected_model_id': selected['id'],
                }
            },
            'warnings': [],
        }

    async def _allowed_model_choices(self, request, user) -> list[dict[str, Any]]:
        raw_models = await self.model_loader(request, user)
        allowed_choices = []
        for model in _iter_models(raw_models):
            try:
                await self.model_access_checker(user, model)
            except Exception:
                continue
            allowed_choices.append(_catalog_choice(model))

        return sorted(allowed_choices, key=_choice_sort_key)

    def _select_allowed_choice(
        self,
        choices: list[dict[str, Any]],
        selection: ModelSelectionRequest,
    ) -> tuple[dict[str, Any], str]:
        if selection.requested_model_id:
            for choice in choices:
                if choice['id'] == selection.requested_model_id:
                    return choice, 'explicit_model_match'

            warning = {
                'code': 'explicit_model_not_allowed',
                'message': f'Requested model is not available for this run: {selection.requested_model_id}',
                'requested_model_id': selection.requested_model_id,
            }
            log.warning(
                'Agent model selection rejected unauthorized explicit model request: run_id=%s '
                'participant_id=%s selection_id=%s requested_model_id=%s',
                selection.run_id,
                selection.participant_id,
                selection.selection_id,
                selection.requested_model_id,
            )
            raise ModelSelectionNotAllowed(warning['message'], warnings=[warning])

        if not choices:
            warning = {
                'code': 'no_permission_valid_models',
                'message': 'No models are available for this run.',
            }
            raise ModelSelectionNotAllowed(warning['message'], warnings=[warning])

        scored = [
            (_fuzzy_score(choice, selection.fuzzy_request), index, choice)
            for index, choice in enumerate(choices)
        ]
        scored.sort(key=lambda item: (-item[0], item[1]))
        score, _index, selected = scored[0]
        if score > 0:
            return selected, 'fuzzy_match'

        return choices[0], 'default_permission_valid_model'


async def _load_all_models(request, user):
    return await get_all_models(request, user=user)


def _iter_models(models: Any) -> list[dict[str, Any]]:
    if isinstance(models, dict):
        values = models.values()
    else:
        values = models or []

    normalized = []
    for model in values:
        if isinstance(model, dict):
            normalized.append(model)
        elif isinstance(model, BaseModel):
            normalized.append(model.model_dump(mode='json'))
    return normalized


def _catalog_choice(model: dict[str, Any]) -> dict[str, Any]:
    meta = _model_meta(model)
    agent_selection = meta.get('agent_selection') or {}
    return {
        'id': model['id'],
        'name': model.get('name') or model['id'],
        'owned_by': model.get('owned_by'),
        'object': model.get('object', 'model'),
        'meta': {
            'agent_selection': agent_selection if isinstance(agent_selection, dict) else {},
        },
    }


def _model_meta(model: dict[str, Any]) -> dict[str, Any]:
    info = model.get('info') or {}
    if not isinstance(info, dict):
        return {}
    meta = info.get('meta') or {}
    return meta if isinstance(meta, dict) else {}


def _choice_sort_key(choice: dict[str, Any]) -> tuple[float, str]:
    agent_selection = choice.get('meta', {}).get('agent_selection') or {}
    priority = agent_selection.get('priority', 1000)
    try:
        priority_value = float(priority)
    except (TypeError, ValueError):
        priority_value = 1000
    return (priority_value, choice['id'])


def _fuzzy_score(choice: dict[str, Any], fuzzy_request: str | None) -> int:
    query_tokens = _tokens(fuzzy_request or '')
    if not query_tokens:
        return 0

    choice_tokens = set()
    choice_tokens.update(_tokens(choice['id']))
    choice_tokens.update(_tokens(choice.get('name') or ''))

    agent_selection = choice.get('meta', {}).get('agent_selection') or {}
    for value in agent_selection.values():
        choice_tokens.update(_tokens_from_value(value))

    return len(query_tokens & choice_tokens)


def _tokens_from_value(value: Any) -> set[str]:
    if isinstance(value, str):
        return _tokens(value)
    if isinstance(value, dict):
        tokens = set()
        for key, nested in value.items():
            tokens.update(_tokens(str(key)))
            tokens.update(_tokens_from_value(nested))
        return tokens
    if isinstance(value, list):
        tokens = set()
        for item in value:
            tokens.update(_tokens_from_value(item))
        return tokens
    return set()


def _tokens(value: str) -> set[str]:
    return set(re.findall(r'[a-z0-9]+', value.lower()))


def _selection_source_request(selection: ModelSelectionRequest) -> dict[str, Any]:
    if selection.source_request:
        return selection.source_request

    source_request: dict[str, Any] = {}
    if selection.requested_model_id:
        source_request['requested_model_id'] = selection.requested_model_id
    if selection.fuzzy_request:
        source_request['request'] = selection.fuzzy_request
    return source_request
