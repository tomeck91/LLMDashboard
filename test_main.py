"""Unit tests for GPUDashboard main module."""
import pytest
from main import NvmlReader, PsutilReader, LmStudioReader, _merge_models, _parse_stats_line


class TestNvmlReader:
    """Tests for NVML reader."""

    def test_init(self):
        """Reader initializes without error."""
        reader = NvmlReader()
        assert reader.count > 0
        assert len(reader.names) == reader.count
        assert len(reader.handles) == reader.count

    def test_poll_returns_correct_structure(self):
        """poll() returns dict with expected keys."""
        reader = NvmlReader()
        sample = reader.poll(0)
        assert "index" in sample
        assert "name" in sample
        assert "util_gpu" in sample
        assert "util_mem" in sample
        assert "mem_used_mib" in sample
        assert "mem_total_mib" in sample
        assert "temp_c" in sample
        assert "power_w" in sample
        assert "clock_graphics_mhz" in sample
        assert "clock_memory_mhz" in sample
        assert "fan_pct" in sample
        assert "power_limit_w" in sample
        assert "procs" in sample


class TestPsutilReader:
    """Tests for psutil reader."""

    def test_poll_returns_correct_structure(self):
        """poll() returns dict with expected keys."""
        reader = PsutilReader()
        sample = reader.poll()
        assert "cpu_pct" in sample
        assert "ram_used_gb" in sample
        assert "ram_total_gb" in sample
        assert "ram_pct" in sample
        assert "boot_time" in sample


class TestLmStudioReader:
    """Tests for unified LM Studio reader."""

    def test_poll_returns_correct_structure(self):
        """poll() returns dict with expected keys."""
        reader = LmStudioReader()
        sample = reader.poll()
        assert "online" in sample
        assert "loaded" in sample
        # Note: may be offline if LM Studio not running, but structure should match


class TestMergeModels:
    """Tests for the model merge logic."""

    def test_http_only(self):
        """HTTP models with no CLI data pass through."""
        http = [
            {"name": "test-model", "quant": "Q4_K_M", "context": 4096, "parallel": 1, "size_gb": 2.3}
        ]
        result = _merge_models(http, [])
        assert len(result) == 1
        assert result[0]["name"] == "test-model"
        assert result[0]["quant"] == "Q4_K_M"
        assert result[0]["status"] is None

    def test_cli_only(self):
        """CLI models with no HTTP data include runtime fields."""
        cli = [
            {"name": "cli-model", "quant": "Q5_K_S", "size_gb": 3.1,
             "context": 8192, "parallel": 2, "status": "idle", "ttl_remaining": "05:00", "device": "Local"}
        ]
        result = _merge_models([], cli)
        assert len(result) == 1
        assert result[0]["name"] == "cli-model"
        assert result[0]["status"] == "idle"
        assert result[0]["ttl_remaining"] == "05:00"

    def test_merge_both_sources(self):
        """Models from both sources merge by name, CLI adds runtime fields."""
        http = [
            {"name": "merged-model", "quant": "Q4_K_M", "context": 4096, "parallel": 1, "size_gb": 2.3}
        ]
        cli = [
            {"name": "merged-model", "quant": "Q5_K_S", "size_gb": 2.5,
             "context": None, "parallel": None, "status": "idle", "ttl_remaining": "10:00", "device": "Local"}
        ]
        result = _merge_models(http, cli)
        assert len(result) == 1
        # HTTP fields preserved (quant, context, parallel from HTTP)
        assert result[0]["quant"] == "Q4_K_M"
        assert result[0]["context"] == 4096
        # CLI runtime fields added
        assert result[0]["status"] == "idle"
        assert result[0]["ttl_remaining"] == "10:00"

    def test_merge_cli_fills_missing_http(self):
        """CLI fills missing fields when HTTP has none."""
        http = [
            {"name": "partial-model", "quant": None, "size_gb": None, "context": None, "parallel": None}
        ]
        cli = [
            {"name": "partial-model", "quant": "Q4_K_M", "size_gb": 2.3,
             "context": 4096, "parallel": 1, "status": "loaded", "ttl_remaining": None, "device": "Local"}
        ]
        result = _merge_models(http, cli)
        assert len(result) == 1
        assert result[0]["quant"] == "Q4_K_M"
        assert result[0]["size_gb"] == 2.3
        assert result[0]["status"] == "loaded"

    def test_multiple_models(self):
        """Multiple models from each source merge correctly."""
        http = [
            {"name": "model-a", "quant": "Q4", "context": 4096, "parallel": 1, "size_gb": 2.0},
            {"name": "model-b", "quant": "Q5", "context": 8192, "parallel": 2, "size_gb": 3.0},
        ]
        cli = [
            {"name": "model-a", "quant": None, "size_gb": None, "context": None, "parallel": None,
             "status": "idle", "ttl_remaining": "01:00", "device": "Local"},
            {"name": "model-c", "quant": "Q6", "size_gb": 4.0, "context": 16384, "parallel": 1,
             "status": "loading", "ttl_remaining": None, "device": "Local"},
        ]
        result = _merge_models(http, cli)
        assert len(result) == 3
        names = {m["name"] for m in result}
        assert names == {"model-a", "model-b", "model-c"}
        # model-a: merged — HTTP quant + CLI status
        a = next(m for m in result if m["name"] == "model-a")
        assert a["quant"] == "Q4" and a["status"] == "idle"
        # model-b: HTTP only
        b = next(m for m in result if m["name"] == "model-b")
        assert b["status"] is None
        # model-c: CLI only
        c = next(m for m in result if m["name"] == "model-c")
        assert c["status"] == "loading"


class TestParseStatsLine:
    """Tests for parsing `lms log stream --stats --json` lines."""

    def test_output_line_with_stats(self):
        line = ('{"timestamp":1,"data":{"type":"llm.prediction.output","output":"hi",'
                '"stats":{"tokensPerSecond":55.7,"timeToFirstTokenSec":0.64,"totalTimeSec":198.7,'
                '"promptTokensCount":23964,"predictedTokensCount":11034},'
                '"modelIdentifier":"qwen/qwen3.8-27b"}}')
        model_id, s = _parse_stats_line(line)
        assert model_id == "qwen/qwen3.8-27b"
        assert s["gen_tps"] == 55.7
        assert s["prompt_tokens"] == 23964
        assert s["gen_tokens"] == 11034
        assert "read_tps" not in s

    def test_input_line_ignored(self):
        line = '{"data":{"type":"llm.prediction.input","input":"hello","modelIdentifier":"m"}}'
        assert _parse_stats_line(line) is None

    def test_garbage_ignored(self):
        assert _parse_stats_line('Streaming logs "stats" from LM Studio') is None
        assert _parse_stats_line("") is None


class TestMergeIds:
    def test_merge_keeps_id(self):
        http = [{"id": "qwen/qwen3.8-27b", "name": "Qwen3.8 27B"}]
        cli = [{"id": "qwen/qwen3.8-27b", "name": "Qwen3.8 27B", "status": "idle"}]
        assert _merge_models(http, cli)[0]["id"] == "qwen/qwen3.8-27b"
        assert _merge_models([], cli)[0]["id"] == "qwen/qwen3.8-27b"
