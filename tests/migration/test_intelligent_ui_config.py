"""Real admin form/routes and config-response seams, with in-memory storage only."""

import ast
import importlib.util
from pathlib import Path
import re
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request, status
from fastapi.testclient import TestClient
from pydantic import BaseModel, Field, StrictStr, field_validator

ROOT = Path(__file__).resolve().parents[2]
KEY = 'ui.enable_intelligent_ui'
PROMPT_KEY = 'ui.intelligent_ui.prompt'
spec = importlib.util.spec_from_file_location('intelligent_ui', ROOT / 'backend/open_webui/utils/intelligent_ui.py')
prompt_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(prompt_module)


def tree(path):
    return ast.parse((ROOT / path).read_text())


class AdminConfigTests(unittest.TestCase):
    def setUp(self):
        self.values = {KEY: True, PROMPT_KEY: None}

        async def current_user(request: Request):
            return SimpleNamespace(role=request.headers.get('x-test-role', 'user'))

        async def save(_request, updates):
            self.values.update(updates)
            return False

        self.save = AsyncMock(side_effect=save)
        router = APIRouter()
        scope = {
            'router': router,
            'Request': Request,
            'Depends': Depends,
            'BaseModel': BaseModel,
            'MfaConfigForm': BaseModel,
            'MFA_CONFIG_KEYS': {},
            'Field': Field,
            'StrictStr': StrictStr,
            'INTELLIGENT_UI_PROMPT': prompt_module.INTELLIGENT_UI_PROMPT,
            'field_validator': field_validator,
            're': re,
            'HTTPException': HTTPException,
            'status': status,
            'ERROR_MESSAGES': SimpleNamespace(ACCESS_PROHIBITED='Access prohibited'),
            'get_current_user': current_user,
            'update_mfa_config': self.save,
            'Config': SimpleNamespace(
                get_many=AsyncMock(side_effect=lambda *keys: {k: self.values[k] for k in keys if k in self.values})
            ),
        }
        auth = tree('backend/open_webui/utils/auth.py')
        guard = next(n for n in auth.body if isinstance(n, ast.FunctionDef) and n.name == 'get_admin_user')
        exec(compile(ast.Module(body=[guard], type_ignores=[]), '<actual-admin-guard>', 'exec'), scope)
        nodes = [
            n
            for n in tree('backend/open_webui/routers/auths.py').body
            if (
                isinstance(n, ast.Assign)
                and any(isinstance(t, ast.Name) and t.id == 'ADMIN_CONFIG_KEYS' for t in n.targets)
            )
            or (
                isinstance(n, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
                and n.name
                in {'AdminConfig', 'get_config_values', 'config_updates', 'get_admin_config', 'update_admin_config'}
            )
        ]
        exec(compile(ast.Module(body=nodes, type_ignores=[]), '<actual-admin-config>', 'exec'), scope)
        self.model = scope['AdminConfig']
        self.payload = {
            name: False if field.annotation is bool else ''
            for name, field in self.model.model_fields.items()
            if field.is_required()
        }
        self.payload.update(DEFAULT_USER_ROLE='user', JWT_EXPIRES_IN='1d')
        app = FastAPI()
        app.include_router(router)
        self.client = TestClient(app)

    def test_default_is_true_and_registered_with_existing_config_defaults(self):
        self.assertTrue(self.model(**self.payload).ENABLE_INTELLIGENT_UI)
        config = tree('backend/open_webui/config.py')
        assignment = next(
            n
            for n in config.body
            if isinstance(n, ast.Assign)
            and any(isinstance(t, ast.Name) and t.id == 'ENABLE_INTELLIGENT_UI' for t in n.targets)
        )
        for configured, expected in [(None, True), ('false', False), ('true', True)]:
            environ = {} if configured is None else {'ENABLE_INTELLIGENT_UI': configured}
            scope = {'os': SimpleNamespace(getenv=environ.get)}
            exec(compile(ast.Module(body=[assignment], type_ignores=[]), '<actual-default>', 'exec'), scope)
            self.assertEqual(scope['ENABLE_INTELLIGENT_UI'], expected)
        defaults = next(
            n.value
            for n in config.body
            if isinstance(n, ast.Assign)
            and any(isinstance(t, ast.Name) and t.id == 'DEFAULT_CONFIG' for t in n.targets)
        )
        value = next(
            v for k, v in zip(defaults.keys, defaults.values) if isinstance(k, ast.Constant) and k.value == KEY
        )
        self.assertEqual(ast.unparse(value), 'ENABLE_INTELLIGENT_UI')
        prompt_default = next(
            v for k, v in zip(defaults.keys, defaults.values) if isinstance(k, ast.Constant) and k.value == PROMPT_KEY
        )
        self.assertIsNone(ast.literal_eval(prompt_default))

    def test_admin_can_read_and_update_shared_boolean(self):
        headers = {'x-test-role': 'admin'}
        self.assertIs(self.client.get('/admin/config', headers=headers).json()['ENABLE_INTELLIGENT_UI'], True)
        for enabled in [False, True]:
            response = self.client.post(
                '/admin/config', headers=headers, json={**self.payload, 'ENABLE_INTELLIGENT_UI': enabled}
            )
            self.assertEqual(response.status_code, 200, response.text)
            self.assertIs(response.json()['ENABLE_INTELLIGENT_UI'], enabled)
            self.assertIs(self.values[KEY], enabled)

    def test_old_admin_payload_omitting_field_preserves_existing_false(self):
        self.values[KEY] = False
        response = self.client.post('/admin/config', headers={'x-test-role': 'admin'}, json=self.payload)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIs(response.json()['ENABLE_INTELLIGENT_UI'], False)
        self.assertNotIn(KEY, self.save.call_args.args[1])

    def test_null_is_not_a_boolean_and_does_not_reset_saved_value(self):
        self.values[KEY] = False
        response = self.client.post(
            '/admin/config', headers={'x-test-role': 'admin'}, json={**self.payload, 'ENABLE_INTELLIGENT_UI': None}
        )
        self.assertEqual(response.status_code, 422)
        self.assertIs(self.values[KEY], False)
        self.save.assert_not_awaited()

    def test_nonadmins_cannot_read_or_change_admin_config(self):
        for role in ['user', 'pending']:
            headers = {'x-test-role': role}
            self.assertEqual(self.client.get('/admin/config', headers=headers).status_code, 401)
            self.assertEqual(
                self.client.post(
                    '/admin/config',
                    headers=headers,
                    json={**self.payload, 'ENABLE_INTELLIGENT_UI': False, 'INTELLIGENT_UI_PROMPT': 'UNAUTHORIZED'},
                ).status_code,
                401,
            )
        self.save.assert_not_awaited()
        self.assertIs(self.values[KEY], True)
        self.assertIsNone(self.values[PROMPT_KEY])

    def test_admin_prompt_save_read_reset_and_omitted_field(self):
        headers = {'x-test-role': 'admin'}
        result = self.client.get('/admin/config', headers=headers).json()
        self.assertIsNone(result['INTELLIGENT_UI_PROMPT'])
        self.assertEqual(result['DEFAULT_INTELLIGENT_UI_PROMPT'], prompt_module.INTELLIGENT_UI_PROMPT)
        custom = 'Custom presentation policy\nKeep whitespace.\n'
        response = self.client.post(
            '/admin/config', headers=headers, json={**self.payload, 'INTELLIGENT_UI_PROMPT': custom}
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.values[PROMPT_KEY], custom)
        self.assertEqual(response.json()['INTELLIGENT_UI_PROMPT'], custom)
        self.assertEqual(self.client.get('/admin/config', headers=headers).json()['INTELLIGENT_UI_PROMPT'], custom)
        response = self.client.post('/admin/config', headers=headers, json=self.payload)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.values[PROMPT_KEY], custom)
        self.assertNotIn(PROMPT_KEY, self.save.call_args.args[1])
        for reset in [None, '', ' \n ']:
            self.values[PROMPT_KEY] = custom
            response = self.client.post(
                '/admin/config', headers=headers, json={**self.payload, 'INTELLIGENT_UI_PROMPT': reset}
            )
            self.assertEqual(response.status_code, 200, response.text)
            self.assertIsNone(self.values[PROMPT_KEY])
            self.assertIsNone(response.json()['INTELLIGENT_UI_PROMPT'])

    def test_invalid_prompt_cannot_overwrite_saved_value(self):
        for value in [123, {}, ['text'], 'x' * 32769]:
            with self.subTest(value_type=type(value).__name__):
                self.values[PROMPT_KEY] = 'Existing'
                response = self.client.post(
                    '/admin/config',
                    headers={'x-test-role': 'admin'},
                    json={**self.payload, 'INTELLIGENT_UI_PROMPT': value},
                )
                self.assertEqual(response.status_code, 422, response.text)
                self.assertEqual(self.values[PROMPT_KEY], 'Existing')
        self.save.assert_not_awaited()

    def test_bundled_default_is_read_only_and_maximum_length_is_accepted(self):
        response = self.client.post(
            '/admin/config',
            headers={'x-test-role': 'admin'},
            json={
                **self.payload,
                'INTELLIGENT_UI_PROMPT': 'x' * 32768,
                'DEFAULT_INTELLIGENT_UI_PROMPT': 'CLIENT_DEFAULT',
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.values[PROMPT_KEY], 'x' * 32768)
        self.assertEqual(response.json()['DEFAULT_INTELLIGENT_UI_PROMPT'], prompt_module.INTELLIGENT_UI_PROMPT)
        self.assertNotIn('DEFAULT_INTELLIGENT_UI_PROMPT', self.save.call_args.args[1])


class PublicFeatureTests(unittest.IsolatedAsyncioTestCase):
    async def test_config_loads_key_and_exposes_shared_value_only_to_logged_in_users(self):
        method = next(
            n
            for n in tree('backend/open_webui/main.py').body
            if isinstance(n, ast.AsyncFunctionDef) and n.name == 'get_app_config'
        )
        load = next(
            n
            for n in method.body
            if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'config' for t in n.targets)
        )
        self.assertIn(KEY, [n.value for n in load.value.value.args if isinstance(n, ast.Constant)])
        result = next(n.value for n in method.body if isinstance(n, ast.Return))
        features = next(
            v for k, v in zip(result.keys, result.values) if isinstance(k, ast.Constant) and k.value == 'features'
        )
        wrapper = ast.parse('async def public_features():\n    pass').body[0]
        wrapper.body = [load, ast.Return(value=features)]
        scope = {n.id: False for n in ast.walk(features) if isinstance(n, ast.Name) and n.id.isupper()}
        scope['WEBSOCKET_HEARTBEAT_INTERVAL'] = None
        for enabled in [True, False]:
            scope['Config'] = SimpleNamespace(get_many=AsyncMock(return_value={KEY: enabled}))
            for role in ['admin', 'user', None]:
                scope['user'] = SimpleNamespace(role=role) if role else None
                exec(
                    compile(
                        ast.fix_missing_locations(ast.Module(body=[wrapper], type_ignores=[])),
                        '<actual-public-features>',
                        'exec',
                    ),
                    scope,
                )
                response = await scope['public_features']()
                if role:
                    self.assertIs(response['enable_intelligent_ui'], enabled)
                else:
                    self.assertNotIn('enable_intelligent_ui', response)


if __name__ == '__main__':
    unittest.main()
