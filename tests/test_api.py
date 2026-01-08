"""
Omega Pro API Test Suite
Run: pytest tests/test_api.py -v
Tests all API v2 endpoints without touching the UI.
"""
import requests
import json
import pytest

BASE = "http://localhost:8080"


class TestProgramsAPI:
    """Test /api/v2/programs endpoints."""
    
    def test_list_programs(self):
        """GET /api/v2/programs returns list."""
        r = requests.get(f"{BASE}/api/v2/programs")
        assert r.status_code == 200
        data = r.json()
        assert isinstance(data, list)
        if data:
            assert 'id' in data[0]
            assert 'title' in data[0]
    
    def test_get_program(self):
        """GET /api/v2/programs/:id returns program details."""
        programs = requests.get(f"{BASE}/api/v2/programs").json()
        if programs:
            program_id = programs[0]['id']
            r = requests.get(f"{BASE}/api/v2/programs/{program_id}")
            assert r.status_code == 200
            data = r.json()
            assert 'title' in data
            assert data['id'] == program_id
    
    def test_get_program_not_found(self):
        """GET /api/v2/programs/:id returns 404 for missing program."""
        r = requests.get(f"{BASE}/api/v2/programs/nonexistent-id")
        assert r.status_code == 404


class TestTracksAPI:
    """Test /api/v2/tracks endpoints."""
    
    def test_active_tracks(self):
        """GET /api/v2/tracks/active returns list."""
        r = requests.get(f"{BASE}/api/v2/tracks/active")
        assert r.status_code == 200
        data = r.json()
        assert isinstance(data, list)
    
    def test_get_tracks_for_program(self):
        """GET /api/v2/programs/:id/tracks returns tracks."""
        programs = requests.get(f"{BASE}/api/v2/programs").json()
        if programs:
            program_id = programs[0]['id']
            r = requests.get(f"{BASE}/api/v2/programs/{program_id}/tracks")
            assert r.status_code == 200
            assert isinstance(r.json(), list)


class TestPipelineAPI:
    """Test pipeline stats endpoint."""
    
    def test_pipeline_stats(self):
        """GET /api/v2/pipeline/stats returns stats."""
        r = requests.get(f"{BASE}/api/v2/pipeline/stats")
        assert r.status_code == 200
        data = r.json()
        assert 'total_active' in data
        assert 'blocked' in data
        assert 'active' in data
        assert 'needs_attention' in data
        assert 'stage_counts' in data
    
    def test_pipeline_stats_stages(self):
        """Pipeline stats includes stages array."""
        r = requests.get(f"{BASE}/api/v2/pipeline/stats")
        data = r.json()
        assert 'stages' in data
        assert isinstance(data['stages'], list)


class TestMetadataAPI:
    """Test languages and voices endpoints."""
    
    def test_languages(self):
        """GET /api/v2/languages returns language list."""
        r = requests.get(f"{BASE}/api/v2/languages")
        assert r.status_code == 200
        data = r.json()
        assert 'languages' in data
        assert len(data['languages']) > 0
        # Verify structure
        lang = data['languages'][0]
        assert 'code' in lang
        assert 'name' in lang
        assert 'default_mode' in lang
    
    def test_voices(self):
        """GET /api/v2/voices returns voices list."""
        r = requests.get(f"{BASE}/api/v2/voices")
        assert r.status_code == 200
        data = r.json()
        assert 'voices' in data
        assert len(data['voices']) > 0
        # Verify structure
        voice = data['voices'][0]
        assert 'id' in voice
        assert 'name' in voice


class TestDeliveriesAPI:
    """Test deliveries endpoint."""
    
    def test_deliveries(self):
        """GET /api/v2/deliveries returns deliveries list."""
        r = requests.get(f"{BASE}/api/v2/deliveries")
        assert r.status_code == 200
        data = r.json()
        assert isinstance(data, list)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
