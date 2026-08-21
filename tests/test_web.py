from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from anko_director.config import AppSettings, ConfigStore
from anko_director.database import Database
from anko_director.web.app import create_app


class TestConfigStore(ConfigStore):
    __test__ = False

    def __init__(self, path: Path) -> None:
        super().__init__(path)
        self.settings = AppSettings()

    def load(self) -> AppSettings:
        return self.settings

    def get_secret(self, name: str) -> str | None:
        return None


def test_anon_flow_through_http_api(tmp_path: Path) -> None:
    db = Database(tmp_path / "web.sqlite3")
    app = create_app(db, TestConfigStore(tmp_path / "config"), start_integrations=False)
    with TestClient(app) as client:
        created = client.post(
            "/api/campaigns", json={"title": "千早爱音安科", "premise": "乐队故事"}
        )
        assert created.status_code == 201
        campaign_id = created.json()["id"]
        parsed = client.post(
            f"/api/campaigns/{campaign_id}/proposals",
            json={
                "instruction": (
                    "建立千早爱音人物卡，能力值包括（家庭背景/音乐能力）\n"
                    "1.天才操盘手\n2.运气爆棚"
                )
            },
        )
        assert parsed.status_code == 200
        proposal = parsed.json()
        assert proposal["proposal"]["character_name"] == "千早爱音"
        rolled = client.post(f"/api/proposals/{proposal['id']}/execute")
        assert rolled.status_code == 200
        assert rolled.json()["character"]["name"] == "千早爱音"
        state = client.get(f"/api/campaigns/{campaign_id}").json()
        assert len(state["events"]) == 1
        assert state["characters"][0]["name"] == "千早爱音"
        page = client.get(f"/campaigns/{campaign_id}")
        assert page.status_code == 200
        assert "千早爱音" in page.text
        assert 'id="chapter-timeline"' in page.text
        assert 'id="chapter-list"' in page.text
        assert 'id="timeline-slider"' not in page.text
