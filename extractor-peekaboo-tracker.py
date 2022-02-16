#!/home/michael/karton-venv/bin/python3

import logging
import random
import sys
import time
import uuid

import karton.core
import karton.core.backend
import karton.core.__version__

EXTRACTOR_CORRELATOR_REPORTS_IDENTITY = "extractor.correlator-for-job-"

config = karton.core.Config(sys.argv[1])

class ExtractorPeekabooTracker(karton.core.Karton):
    identity = "extractor.peekaboo-tracker"
    filters = [
        {
            "type": "peekaboo-job",
            "state": "to-track",
        }
    ]

    def process(self, task: karton.core.Task) -> None:
        peekaboo_job_id = uuid.UUID(task.payload.get("peekaboo-job-id"))

        # tracked ;)
        if random.randint(0, 2):
            # bounce back to the poker
            delay_task = task.derive_task({
                "type": "peekaboo-job",
                "state": "delayed",
                "delay-queue": task.headers.get("delay-queue"),
            })
            self.send_task(delay_task)
            self.log.info(
                "%s:%s: Told poker that it needs more tracking (%s)",
                task.root_uid, peekaboo_job_id, delay_task.uid)
            return

        # notify poker that this job is done and needs no more poking
        done_task = karton.core.Task(
            headers={
                "type": "peekaboo-job",
                "state": "done",
            },
            payload={
                "peekaboo-job-id": str(peekaboo_job_id)
            })
        self.send_task(done_task)
        self.log.info(
            "%s:%s: Told poker that it's done (%s)",
            task.root_uid, peekaboo_job_id, done_task.uid)

        # register a persistent queue for this extractor job (if there isn't one
        # already) where to park the job reports until all jobs are done
        bind = None
        report_identity = EXTRACTOR_CORRELATOR_REPORTS_IDENTITY + str(task.root_uid)
        try:
            bind = self.backend.get_bind(report_identity)
        except TypeError:
            pass

        # have to match the correlator per-job bind filters or we'll get "Task
        # rejected because binds are no longer valid." there
        headers = {
            "type": "peekaboo-report",
            # only for routing to the job-specific persistent correlator parking queue
            "extractor-job-id": task.root_uid,
        }

        if bind is None:
            bind = karton.core.backend.KartonBind(
                identity=report_identity,
                info=("Correlator queue persistence bind for job "
                      f"{task.root_uid} created by tracker"),
                version=karton.core.__version__.__version__,
                # filters are headers of our tasks, obvs.
                filters=[headers],
                persistent=True,
                service_version=self.__class__.version)
            self.backend.register_bind(bind)

        report_task = karton.core.Task(
            headers=headers,
            payload={
                "peekaboo-job-id": str(peekaboo_job_id),
                "result": "bad",
                "reason": "because",
                "report": "yessir",
            })

        self.send_task(report_task)
        self.log.info(
            "%s:%s: Submitted report (%s)",
            task.root_uid, peekaboo_job_id, report_task.uid)


if __name__ == "__main__":
    c = ExtractorPeekabooTracker(config)
    c.loop()
