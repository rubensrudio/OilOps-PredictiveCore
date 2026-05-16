"""
Tests for shared/config.py

Verifies:
- Settings can be instantiated with defaults (no env vars)
- Default values match the plan spec
- All env var names map correctly
- fft_bins default is 64 (the criterion from tasks.md)
"""



class TestSettingsDefaults:
    def test_settings_instantiable_with_no_env(self, monkeypatch):
        """Settings must work without any OILOPS_* env vars set."""
        for key in (
            "OILOPS_API_KEY",
            "OILOPS_DATA_DIR",
            "OILOPS_MAX_BACKFILL_DAYS",
            "OILOPS_FEATURE_WINDOW_SIZE",
            "OILOPS_FFT_BINS",
            "OILOPS_EXPLAIN_TOP_N",
        ):
            monkeypatch.delenv(key, raising=False)

        from shared.config import Settings

        s = Settings()
        assert s is not None

    def test_fft_bins_default_is_64(self, monkeypatch):
        """Criterion from tasks.md: s.fft_bins == 64."""
        monkeypatch.delenv("OILOPS_FFT_BINS", raising=False)
        from shared.config import Settings

        s = Settings()
        assert s.fft_bins == 64

    def test_max_backfill_days_default_is_30(self, monkeypatch):
        monkeypatch.delenv("OILOPS_MAX_BACKFILL_DAYS", raising=False)
        from shared.config import Settings

        s = Settings()
        assert s.max_backfill_days == 30

    def test_feature_window_size_default_is_64(self, monkeypatch):
        monkeypatch.delenv("OILOPS_FEATURE_WINDOW_SIZE", raising=False)
        from shared.config import Settings

        s = Settings()
        assert s.feature_window_size == 64

    def test_explain_top_n_default_is_5(self, monkeypatch):
        monkeypatch.delenv("OILOPS_EXPLAIN_TOP_N", raising=False)
        from shared.config import Settings

        s = Settings()
        assert s.explain_top_n == 5

    def test_data_dir_default_is_slash_data(self, monkeypatch):
        monkeypatch.delenv("OILOPS_DATA_DIR", raising=False)
        from shared.config import Settings

        s = Settings()
        assert s.data_dir == "/data"

    def test_api_key_default_is_none(self, monkeypatch):
        monkeypatch.delenv("OILOPS_API_KEY", raising=False)
        from shared.config import Settings

        s = Settings()
        assert s.api_key is None


class TestSettingsEnvOverride:
    def test_fft_bins_overridden_by_env(self, monkeypatch):
        monkeypatch.setenv("OILOPS_FFT_BINS", "128")
        from importlib import reload
        import shared.config as config_module

        reload(config_module)
        from shared.config import Settings

        s = Settings()
        assert s.fft_bins == 128

    def test_max_backfill_days_overridden_by_env(self, monkeypatch):
        monkeypatch.setenv("OILOPS_MAX_BACKFILL_DAYS", "60")
        from shared.config import Settings

        s = Settings()
        assert s.max_backfill_days == 60

    def test_api_key_set_by_env(self, monkeypatch):
        monkeypatch.setenv("OILOPS_API_KEY", "secret-key-test")
        from shared.config import Settings

        s = Settings()
        assert s.api_key == "secret-key-test"

    def test_data_dir_overridden_by_env(self, monkeypatch):
        monkeypatch.setenv("OILOPS_DATA_DIR", "/custom/data")
        from shared.config import Settings

        s = Settings()
        assert s.data_dir == "/custom/data"


class TestSettingsTypes:
    def test_fft_bins_is_int(self, monkeypatch):
        monkeypatch.delenv("OILOPS_FFT_BINS", raising=False)
        from shared.config import Settings

        s = Settings()
        assert isinstance(s.fft_bins, int)

    def test_max_backfill_days_is_int(self, monkeypatch):
        monkeypatch.delenv("OILOPS_MAX_BACKFILL_DAYS", raising=False)
        from shared.config import Settings

        s = Settings()
        assert isinstance(s.max_backfill_days, int)

    def test_feature_window_size_is_int(self, monkeypatch):
        monkeypatch.delenv("OILOPS_FEATURE_WINDOW_SIZE", raising=False)
        from shared.config import Settings

        s = Settings()
        assert isinstance(s.feature_window_size, int)

    def test_explain_top_n_is_int(self, monkeypatch):
        monkeypatch.delenv("OILOPS_EXPLAIN_TOP_N", raising=False)
        from shared.config import Settings

        s = Settings()
        assert isinstance(s.explain_top_n, int)
