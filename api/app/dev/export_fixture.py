"""Print the fake run as JSONL: uv run python -m app.dev.export_fixture"""

from app.dev.fake_run import build_fake_events
from app.events.models import EVENT_ADAPTER

if __name__ == "__main__":
    for event in build_fake_events():
        print(EVENT_ADAPTER.dump_json(event).decode())
