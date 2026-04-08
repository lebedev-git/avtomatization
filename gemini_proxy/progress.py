from __future__ import annotations

from collections.abc import Iterable

from .schemas import ProcessingTimeline, ProcessingTimelineStep


def now_timestamp(factory) -> str:
    return factory()


class TimelineTracker:
    def __init__(self, now_factory) -> None:
        self._now_factory = now_factory
        self._live: dict[str, ProcessingTimeline] = {}

    def start(
        self,
        key: str,
        steps: Iterable[tuple[str, str]],
        *,
        summary: str | None = None,
        first_message: str | None = None,
    ) -> ProcessingTimeline:
        step_defs = list(steps)
        started_at = self._now()
        timeline_steps = [
            ProcessingTimelineStep(
                id=step_id,
                label=label,
                status="active" if index == 0 else "pending",
                message=first_message if index == 0 else None,
                started_at=started_at if index == 0 else None,
            )
            for index, (step_id, label) in enumerate(step_defs)
        ]
        timeline = ProcessingTimeline(
            running=True,
            current_step_id=step_defs[0][0] if step_defs else None,
            summary=summary,
            updated_at=started_at,
            steps=timeline_steps,
        )
        self._live[key] = timeline
        return timeline.model_copy(deep=True)

    def advance(self, key: str, step_id: str, *, message: str | None = None) -> ProcessingTimeline:
        timeline = self._require(key)
        changed_at = self._now()
        for step in timeline.steps:
            if step.id == step_id:
                if step.status == "pending":
                    step.started_at = step.started_at or changed_at
                step.status = "active"
                if message is not None:
                    step.message = message
            elif step.status == "active":
                step.status = "completed"
                step.finished_at = changed_at
        timeline.current_step_id = step_id
        timeline.updated_at = changed_at
        return timeline.model_copy(deep=True)

    def touch(self, key: str, step_id: str, *, message: str | None = None, summary: str | None = None) -> ProcessingTimeline:
        timeline = self._require(key)
        changed_at = self._now()
        for step in timeline.steps:
            if step.id != step_id:
                continue
            if step.status == "pending":
                step.status = "active"
                step.started_at = step.started_at or changed_at
                timeline.current_step_id = step_id
            if message is not None:
                step.message = message
            break
        if summary is not None:
            timeline.summary = summary
        timeline.updated_at = changed_at
        return timeline.model_copy(deep=True)

    def finish(self, key: str, step_id: str, *, message: str | None = None, summary: str | None = None) -> ProcessingTimeline:
        timeline = self.advance(key, step_id, message=message)
        changed_at = self._now()
        stored = self._require(key)
        for step in stored.steps:
            if step.id == step_id:
                step.status = "completed"
                step.finished_at = changed_at
                if message is not None:
                    step.message = message
                break
        stored.running = False
        stored.current_step_id = step_id
        stored.summary = summary or stored.summary
        stored.updated_at = changed_at
        return stored.model_copy(deep=True)

    def fail(self, key: str, step_id: str, *, message: str) -> ProcessingTimeline:
        timeline = self._require(key)
        changed_at = self._now()
        for step in timeline.steps:
            if step.id == step_id:
                step.status = "error"
                step.started_at = step.started_at or changed_at
                step.finished_at = changed_at
                step.message = message
            elif step.status == "active":
                step.status = "completed"
                step.finished_at = changed_at
        timeline.running = False
        timeline.current_step_id = step_id
        timeline.summary = message
        timeline.updated_at = changed_at
        return timeline.model_copy(deep=True)

    def complete_without_run(
        self,
        steps: Iterable[tuple[str, str]],
        completed_step_ids: set[str],
        *,
        current_step_id: str | None = None,
        summary: str | None = None,
        updated_at: str | None = None,
    ) -> ProcessingTimeline:
        return ProcessingTimeline(
            running=False,
            current_step_id=current_step_id,
            summary=summary,
            updated_at=updated_at,
            steps=[
                ProcessingTimelineStep(
                    id=step_id,
                    label=label,
                    status="completed" if step_id in completed_step_ids else "pending",
                )
                for step_id, label in steps
            ],
        )

    def get(self, key: str) -> ProcessingTimeline | None:
        timeline = self._live.get(key)
        return timeline.model_copy(deep=True) if timeline is not None else None

    def clear(self, key: str) -> None:
        self._live.pop(key, None)

    def _require(self, key: str) -> ProcessingTimeline:
        timeline = self._live.get(key)
        if timeline is None:
            raise KeyError(key)
        return timeline

    def _now(self) -> str:
        return now_timestamp(self._now_factory)
