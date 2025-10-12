from backend.common.config.base_config import load_config

def test_config_has_lock_key():
    cfg = load_config()
    assert hasattr(cfg, 'auto_retrain_lock_key')
    assert isinstance(cfg.auto_retrain_lock_key, int)
