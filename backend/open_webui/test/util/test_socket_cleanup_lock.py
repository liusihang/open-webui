from pathlib import Path


def test_session_cleanup_renews_before_redis_lock_ttl():
    source = Path('backend/open_webui/socket/main.py').read_text()

    assert 'SESSION_CLEANUP_LOCK_RENEW_INTERVAL' in source
    assert 'await asyncio.sleep(SESSION_CLEANUP_LOCK_RENEW_INTERVAL)' in source
