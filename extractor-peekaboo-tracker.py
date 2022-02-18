#!/home/michael/karton-venv/bin/python3

import random
import sys
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
        peekaboo_job_id = None
        peekaboo_job_id_payload = task.get_payload("peekaboo-job-id")
        if peekaboo_job_id_payload is not None:
            peekaboo_job_id = uuid.UUID(peekaboo_job_id_payload)

        report_payload = {}
        if peekaboo_job_id is None:
            # submit to peekaboo had failed :(
            report_payload["result"] = "failed"
            reason = task.get_payload("failure-reason")
            if reason is not None:
                report_payload[
                    "reason"] = f"submit to peekaboo failed: {reason}"
        else:
            # tracked ;)
            if random.randint(0, 2):
                # not done yet - bounce back to the poker
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

            if random.randint(0, 3):
                # finished normally
                report_payload["result"] = [
                    "bad", "ignored", "unknown"][random.randint(0, 2)]
                report_payload["reason"] = "because"
                report_payload["report"] = ["yessir"]
            else:
                if random.randint(0, 2):
                    # job failed inside Peekaboo :(
                    error = [
                        "submit to cuckoo failed",
                        "cuckoo job too old"][random.randint(0, 1)]

                    report_payload["result"] = "failed"
                    report_payload["reason"] = f"Peekaboo job failed: {error}"
                else:
                    # tacking failed :(
                    error = [
                        "host unreachable",
                        "connection refused",
                        "internal server error"][random.randint(0, 2)]

                    report_payload["result"] = "failed"
                    report_payload[
                        "reason"] = f"Tracking Peekaboo job failed: {error}"

        # notify poker that this job is done and needs no more poking
        done_task = karton.core.Task(
            headers={
                "type": "peekaboo-job",
                "state": "done",
            })

        # metadata from persistent payload (job id, file-name) is added
        # automatically here
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
            payload=report_payload)

        # metadata from persistent payload is added automatically here
        self.send_task(report_task)
        self.log.info(
            "%s:%s: Submitted report (%s)",
            task.root_uid, peekaboo_job_id, report_task.uid)

        # do not do lengthy processing down here because it aggravates a race
        # condition with the all-jobs-finished check in the poker


if __name__ == "__main__":
    c = ExtractorPeekabooTracker(config)
    c.loop()
