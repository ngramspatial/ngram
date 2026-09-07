from __future__ import annotations

import json

import pytest

from ngram.presence.tools import youtube


def test_youtube_search_page_extracts_video_metadata() -> None:
    initial_data = {
        "contents": [
            {
                "videoRenderer": {
                    "videoId": "dQw4w9WgXcQ",
                    "title": {"runs": [{"text": "Test Song"}]},
                    "ownerText": {"runs": [{"text": "Test Artist"}]},
                    "lengthText": {"simpleText": "3:33"},
                    "descriptionSnippet": {"runs": [{"text": "Official audio"}]},
                }
            }
        ]
    }
    page = f"<script>var ytInitialData = {json.dumps(initial_data)};</script>"

    assert youtube._youtube_results_from_page(page, 5) == [
        {
            "title": "Test Song",
            "url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            "video_id": "dQw4w9WgXcQ",
            "channel": "Test Artist",
            "duration": "3:33",
            "description": "Official audio",
        }
    ]


@pytest.mark.asyncio
async def test_search_youtube_prefers_direct_results(monkeypatch) -> None:
    expected = [
        {
            "title": "Result",
            "url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            "video_id": "dQw4w9WgXcQ",
        }
    ]

    async def fake_direct(query: str, max_results: int):
        assert query == "test song"
        assert max_results == 3
        return expected

    monkeypatch.setattr(youtube, "_search_youtube_direct", fake_direct)
    result = json.loads(await youtube.search_youtube("test song", 3))

    assert result["source"] == "youtube"
    assert result["results"] == expected
