import hashlib
import json
import zipfile
from io import BytesIO
from unittest.mock import patch

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from open_webui.internal.db import JSONField
from open_webui.migrations.versions import e2f3a4b5c7_add_skill_package_table as skill_package_migration
from open_webui.models.skills import SkillPackage, SkillPackageModel, skill_package_id
from open_webui.utils.skill_packages import (
    SkillPackageError,
    build_skill_package_manifest,
    build_skill_package_zip_bytes,
    normalize_package_files,
    parse_skill_json,
    parse_skill_markdown,
    skill_package_storage_filename,
    validate_package_file_path,
)
from sqlalchemy import BigInteger, String, Text, UniqueConstraint, create_engine, inspect


def _run_migration(engine, direction):
    with engine.begin() as connection:
        context = MigrationContext.configure(connection)
        operations = Operations(context)
        with patch.object(skill_package_migration, 'op', operations):
            getattr(skill_package_migration, direction)()


def test_skill_package_model_declares_expected_columns_constraints_and_indexes():
    table = SkillPackage.__table__

    assert table.name == 'skill_package'
    assert set(table.columns.keys()) >= {
        'id',
        'skill_id',
        'bundle_hash',
        'manifest',
        'storage_path',
        'created_at',
        'updated_at',
    }
    assert table.c.id.primary_key
    assert isinstance(table.c.id.type, String)
    assert isinstance(table.c.skill_id.type, String)
    assert isinstance(table.c.bundle_hash.type, String)
    assert isinstance(table.c.manifest.type, JSONField)
    assert isinstance(table.c.storage_path.type, Text)
    assert isinstance(table.c.created_at.type, BigInteger)
    assert not table.c.skill_id.nullable
    assert not table.c.bundle_hash.nullable
    assert not table.c.manifest.nullable
    assert not table.c.storage_path.nullable

    unique_constraints = {
        tuple(constraint.columns.keys())
        for constraint in table.constraints
        if isinstance(constraint, UniqueConstraint)
    }
    assert ('skill_id', 'bundle_hash') in unique_constraints

    index_columns = {index.name: tuple(index.columns.keys()) for index in table.indexes}
    assert index_columns['ix_skill_package_skill_id'] == ('skill_id',)
    assert index_columns['ix_skill_package_bundle_hash'] == ('bundle_hash',)


def test_skill_package_model_can_validate_from_orm_attributes():
    row = SkillPackage(
        id='pkg_1',
        skill_id='skill_1',
        bundle_hash='a' * 64,
        manifest={'files': []},
        storage_path='skillpkg_skill_1_aaaaaaaa.zip',
        created_at=1,
        updated_at=2,
    )

    model = SkillPackageModel.model_validate(row)

    assert model.id == 'pkg_1'
    assert model.skill_id == 'skill_1'
    assert model.manifest == {'files': []}


def test_skill_package_id_is_stable_and_uses_full_hashes_for_arbitrary_skill_ids():
    bundle_hash = 'a' * 64
    skill_id = '../Skill With Spaces:@?*'
    skill_id_digest = hashlib.sha256(skill_id.encode('utf-8')).hexdigest()

    package_id = skill_package_id(skill_id, bundle_hash)

    assert package_id == f'skillpkg_{skill_id_digest}_{bundle_hash}'
    assert skill_id not in package_id


def test_skill_package_migration_upgrade_and_downgrade_tolerate_existing_surface():
    engine = create_engine('sqlite:///:memory:')

    _run_migration(engine, 'upgrade')
    _run_migration(engine, 'upgrade')

    inspector = inspect(engine)
    assert 'skill_package' in inspector.get_table_names()
    assert {'ix_skill_package_skill_id', 'ix_skill_package_bundle_hash'} <= {
        index['name'] for index in inspector.get_indexes('skill_package')
    }

    _run_migration(engine, 'downgrade')
    _run_migration(engine, 'downgrade')
    assert 'skill_package' not in inspect(engine).get_table_names()


def test_parse_skill_markdown_extracts_frontmatter_and_body():
    parsed = parse_skill_markdown(
        '---\n'
        'name: Terminal Helper\n'
        'description: Runs small terminal tasks\n'
        '---\n'
        '# Terminal Helper\n\n'
        'Use the terminal carefully.\n'
    )

    assert parsed.name == 'Terminal Helper'
    assert parsed.description == 'Runs small terminal tasks'
    assert parsed.body == '# Terminal Helper\n\nUse the terminal carefully.\n'


@pytest.mark.parametrize(
    ('description_yaml', 'expected_description'),
    [
        (
            'description: >\n'
            '  Runs small terminal tasks\n'
            '  from packaged skills.\n',
            'Runs small terminal tasks from packaged skills.',
        ),
        (
            'description: |\n'
            '  Runs small terminal tasks.\n'
            '  Preserve line breaks.\n',
            'Runs small terminal tasks.\nPreserve line breaks.',
        ),
    ],
)
def test_parse_skill_markdown_accepts_yaml_multiline_frontmatter(description_yaml, expected_description):
    parsed = parse_skill_markdown(
        '---\n'
        'name: Terminal Helper\n'
        f'{description_yaml}'
        '---\n'
        '# Terminal Helper\n\n'
        'Use the terminal carefully.\n'
    )

    assert parsed.name == 'Terminal Helper'
    assert parsed.description == expected_description
    assert parsed.body == '# Terminal Helper\n\nUse the terminal carefully.\n'


@pytest.mark.parametrize(
    'frontmatter',
    [
        'just a string\n',
        '- name: Terminal Helper\n',
        '',
    ],
)
def test_parse_skill_markdown_rejects_non_object_frontmatter(frontmatter):
    with pytest.raises(SkillPackageError, match='frontmatter must be a YAML object'):
        parse_skill_markdown(f'---\n{frontmatter}---\nBody\n')


def test_parse_skill_json_accepts_entrypoints_only():
    entrypoints = [
        {
            'name': 'default',
            'path': 'scripts/run.py',
            'runtime': 'python',
        }
    ]

    parsed = parse_skill_json(json.dumps({'entrypoints': entrypoints}))

    assert parsed.entrypoints == entrypoints


@pytest.mark.parametrize(
    'entrypoints',
    [
        {'default': {'path': 'scripts/run.py', 'runtime': 'python'}},
        [{'name': 'default', 'path': 'scripts/run.py', 'runtime': 'python', 'args': []}],
        [{'name': 'default', 'path': 'scripts/run.py'}],
        [{'name': 'default', 'runtime': 'python'}],
    ],
)
def test_parse_skill_json_rejects_unsupported_entrypoint_schema(entrypoints):
    with pytest.raises(SkillPackageError):
        parse_skill_json(json.dumps({'entrypoints': entrypoints}))


@pytest.mark.parametrize(
    'unsafe_path',
    [
        '/scripts/run.py',
        '../scripts/run.py',
        'scripts/../run.py',
        'scripts\\run.py',
    ],
)
def test_parse_skill_json_rejects_unsafe_entrypoint_paths(unsafe_path):
    with pytest.raises(SkillPackageError):
        parse_skill_json(
            json.dumps(
                {
                    'entrypoints': [
                        {
                            'name': 'default',
                            'path': unsafe_path,
                            'runtime': 'python',
                        }
                    ]
                }
            )
        )


@pytest.mark.parametrize('field_name', ['id', 'name', 'description', 'version'])
def test_parse_skill_json_rejects_descriptive_package_fields(field_name):
    with pytest.raises(SkillPackageError, match='skill.json must not include'):
        parse_skill_json(json.dumps({'entrypoints': [], field_name: 'duplicate'}))


@pytest.mark.parametrize(
    'unsafe_path',
    [
        '',
        '.',
        '/absolute/SKILL.md',
        '../SKILL.md',
        ' SKILL.md',
        'SKILL.md ',
        '\tSKILL.md',
        'SKILL.md\n',
        'scripts/../run.py',
        'scripts\\run.py',
    ],
)
def test_validate_package_file_path_rejects_unsafe_paths(unsafe_path):
    with pytest.raises(SkillPackageError):
        validate_package_file_path(unsafe_path)


def test_normalize_package_files_rejects_unsupported_binary_files():
    with pytest.raises(SkillPackageError, match='unsupported package file type'):
        normalize_package_files({'assets/logo.png': b'\x89PNG\r\n'})

    with pytest.raises(SkillPackageError, match='text package files must be UTF-8'):
        normalize_package_files({'scripts/run.py': b'\xff\xfe\x00'})


def test_normalize_package_files_rejects_paths_that_would_strip_into_duplicates():
    with pytest.raises(SkillPackageError, match='whitespace'):
        normalize_package_files(
            {
                'SKILL.md': 'original\n',
                ' SKILL.md': 'shadow\n',
            }
        )


def test_build_skill_package_manifest_is_deterministic_after_text_normalization():
    files_a = {
        'scripts/run.py': "print('hello')\r\n",
        'skill.json': json.dumps(
            {
                'entrypoints': [
                    {
                        'name': 'default',
                        'path': 'scripts/run.py',
                        'runtime': 'python',
                    }
                ]
            }
        ),
        'SKILL.md': '---\nname: Demo\ndescription: Demo skill\n---\nBody\n',
    }
    files_b = {
        'SKILL.md': '---\nname: Demo\ndescription: Demo skill\n---\nBody\n',
        'skill.json': json.dumps(
            {
                'entrypoints': [
                    {
                        'name': 'default',
                        'path': 'scripts/run.py',
                        'runtime': 'python',
                    }
                ]
            }
        ),
        'scripts/run.py': "print('hello')\n",
    }

    manifest_a = build_skill_package_manifest(files_a)
    manifest_b = build_skill_package_manifest(files_b)

    assert manifest_a.hash == manifest_b.hash
    assert [file.path for file in manifest_a.files] == ['SKILL.md', 'scripts/run.py', 'skill.json']
    assert manifest_a.entrypoints == [{'name': 'default', 'path': 'scripts/run.py', 'runtime': 'python'}]
    assert manifest_a.model_dump() == manifest_b.model_dump()
    assert manifest_a.hash != build_skill_package_manifest({**files_b, 'scripts/run.py': "print('bye')\n"}).hash


def test_build_skill_package_manifest_rejects_entrypoint_paths_not_in_package():
    files = {
        'SKILL.md': '---\nname: Demo\ndescription: Demo skill\n---\nBody\n',
        'skill.json': json.dumps(
            {
                'entrypoints': [
                    {
                        'name': 'default',
                        'path': 'scripts/missing.py',
                        'runtime': 'python',
                    }
                ]
            }
        ),
    }

    with pytest.raises(SkillPackageError, match='must point to a packaged file'):
        build_skill_package_manifest(files)


def test_build_skill_package_zip_bytes_is_deterministic_and_contains_normalized_text():
    files_a = {
        'scripts/run.py': "print('hello')\r\n",
        'SKILL.md': '---\nname: Demo\ndescription: Demo skill\n---\nBody\n',
    }
    files_b = {
        'SKILL.md': '---\nname: Demo\ndescription: Demo skill\n---\nBody\n',
        'scripts/run.py': "print('hello')\n",
    }

    bundle_a = build_skill_package_zip_bytes(files_a)
    bundle_b = build_skill_package_zip_bytes(files_b)

    assert bundle_a == bundle_b
    with zipfile.ZipFile(BytesIO(bundle_a)) as archive:
        assert archive.namelist() == ['SKILL.md', 'scripts/run.py']
        assert archive.read('scripts/run.py') == b"print('hello')\n"
        assert {info.create_system for info in archive.infolist()} == {3}


def test_build_skill_package_zip_bytes_sets_create_system_independent_of_platform_default(monkeypatch):
    original_init = zipfile.ZipInfo.__init__

    def init_with_non_unix_default(self, *args, **kwargs):
        original_init(self, *args, **kwargs)
        self.create_system = 0

    monkeypatch.setattr(zipfile.ZipInfo, '__init__', init_with_non_unix_default)

    bundle = build_skill_package_zip_bytes(
        {
            'SKILL.md': '---\nname: Demo\ndescription: Demo skill\n---\nBody\n',
            'scripts/run.py': "print('hello')\n",
        }
    )

    with zipfile.ZipFile(BytesIO(bundle)) as archive:
        assert {info.create_system for info in archive.infolist()} == {3}


def test_skill_package_storage_filename_is_flat_and_hash_bound():
    bundle_hash = 'a' * 64

    safe_id_digest = hashlib.sha256(b'skill_1').hexdigest()
    assert skill_package_storage_filename('skill_1', bundle_hash) == (
        f'skillpkg_skill_1-{safe_id_digest}_{bundle_hash}.zip'
    )

    unsafe_skill_id = '../Skill With Spaces:@?*'
    unsafe_id_digest = hashlib.sha256(unsafe_skill_id.encode('utf-8')).hexdigest()
    unsafe_filename = skill_package_storage_filename(unsafe_skill_id, bundle_hash)
    assert unsafe_filename == f'skillpkg_Skill-With-Spaces-{unsafe_id_digest}_{bundle_hash}.zip'
    assert '/' not in unsafe_filename
    assert '\\' not in unsafe_filename

    long_skill_id = f"{'skill.' * 80}!tail"
    long_id_digest = hashlib.sha256(long_skill_id.encode('utf-8')).hexdigest()
    long_filename = skill_package_storage_filename(long_skill_id, bundle_hash)
    assert long_id_digest in long_filename
    assert long_filename.endswith(f'_{bundle_hash}.zip')
    assert len(long_filename) < 230

    with pytest.raises(SkillPackageError):
        skill_package_storage_filename('skill_1', 'not-a-sha256')
