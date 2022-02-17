#!/home/michael/karton-venv/bin/python3

import json
import sys
import uuid

import karton.core
import karton.core.backend
import karton.core.base

EXTRACTOR_JOB_REPORT = "extractor.report:"

EXTRACTOR_CORRELATOR_REPORTS_IDENTITY = "extractor.correlator-for-job-"

config = karton.core.Config(sys.argv[1])

class NonBlockingKartonBackend(karton.core.backend.KartonBackend):
    def consume_queues(self, queues, timeout = 0):
        for queue in queues:
            item = self.redis.lpop(queue)
            if item:
                return queue, item

        return None

class ExtractorJobCorrelator(karton.core.Consumer):
    def __init__(self, job_id, config=None, backend=None):
        self.job_id = job_id

        # read instead of class members
        self.filters = [
            {
                "type": "peekaboo-report",
                "extractor-job-id": str(job_id),
            }
        ]

        # in-memory state :( short-lived at least - could also be in redis instead
        self.jobs = {}

        identity = EXTRACTOR_CORRELATOR_REPORTS_IDENTITY + str(job_id)

        # avoid GracefulKiller from KartonServiceBase
        super(karton.core.base.KartonServiceBase, self).__init__(
            config=config, identity=identity, backend=backend)
        self.current_task = None
        self.setup_logger()
        self._pre_hooks = []
        self._post_hooks = []

    def process(self, task: karton.core.Task) -> None:
        peekaboo_job_id = uuid.UUID(task.get_payload("peekaboo-job-id"))
        self.log.info("%s:%s: Correlating.", self.job_id, peekaboo_job_id)

        # do more schema checking here

        job = self.jobs[str(peekaboo_job_id)] = {}
        for datum in [
                "result", "reason", "report", "file-name", "content-type",
                "content-disposition"]:
            value = task.get_payload(datum)
            if value is not None:
                job[datum] = value

    def correlate(self) -> dict:
        """
        Reduced loop that doesn't block and exits as soon as there are no more
        tasks
        """
        self.backend.register_bind(self._bind)
        self.backend.set_consumer_identity(self.identity)

        while True:
            task = self.backend.consume_routed_task(self.identity)
            if not task:
                break

            self.internal_process(task)

        # pick worst
        jobs_by_results = {}
        for job_id, job in self.jobs.items():
            result = job.get("result")
            if result is None:
                continue

            if jobs_by_results.get(result) is None:
                jobs_by_results[result] = []

            jobs_by_results[result].append(job_id)

        overall_result = None
        worst_job = None
        for result in ["bad", "failed", "good", "ignored", "unchecked", "unknown"]:
            job_ids = jobs_by_results.get(result)

            # can be None or potentially empty list
            if job_ids:
                overall_result = result
                worst_job = job_ids[0]
                break

        job_info = dict(
            result="unknown",
            reason="unknown status from Peekaboo",
            report=[])

        # unknown result!?
        if worst_job is not None:
            job_info = {}
            job_info["result"] = overall_result
            reason = self.jobs[worst_job].get("reason")
            if reason is not None:
                job_info["reason"] = reason

            report = job_info["report"] = {}
            report["id-of-worst-job"] = worst_job
            report["jobs"] = self.jobs
        
        report_key = EXTRACTOR_JOB_REPORT + str(self.job_id)
        self.backend.redis.set(report_key, json.dumps(job_info))

        self.log.info("%s: Correlated.", self.job_id)
        self.backend.unregister_bind(self.identity)


class ExtractorCorrelator(karton.core.Consumer):
    identity = "extractor.correlator"
    filters = [
        {
            "type": "extractor-correlator-poke",
        }
    ]

    def __init__(self, config=None, identity=None, backend=None):
        self.non_blocking_backend = NonBlockingKartonBackend(config)
        super().__init__(config=config, identity=identity, backend=backend)

    def process(self, task: karton.core.Task) -> None:
        self.log.info("%s: Got poked to look at extractor job", task.root_uid)

        # spawn a job-specific consumer. Because we're doing it because of a
        # trigger task from the poker, we can be sure we're the only ones doing
        # that.
        job_correlator = ExtractorJobCorrelator(
            task.root_uid, self.config, self.non_blocking_backend)
        job_correlator.correlate()


if __name__ == "__main__":
    c = ExtractorCorrelator(config)
    c.loop()
