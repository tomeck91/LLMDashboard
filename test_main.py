"""Unit tests for GPUDashboard main module."""
import pytest
from main import NvmlReader, PsutilReader, LmStudioReader, LmsReader


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
    """Tests for LM Studio reader."""

    def test_poll_returns_correct_structure(self):
        """poll() returns dict with expected keys."""
        reader = LmStudioReader()
        sample = reader.poll()
        assert "online" in sample
        assert "loaded" in sample
        # Note: may be offline if LM Studio not running, but structure should match


class TestLmsReader:
    """Tests for LMS CLI reader."""

    def test_poll_returns_correct_structure(self):
        """poll() returns dict with expected keys."""
        reader = LmsReader()
        sample = reader.poll()
        assert "loaded" in sample
        # Note: may be empty if lms not installed or no models loaded
