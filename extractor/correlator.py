""" A Karton that correlates job reports into a summary report. """

import datetime
import hashlib
import json
import sys

import karton.core
import karton.core.base

from . import __version__


class NonBlockingKartonBackend(karton.core.backend.KartonBackend):
    def consume_queues(self, queues, timeout=0):
        """ modified to be non-blocking """
        for queue in queues:
            item = self.redis.lpop(queue)
            if item:
                return queue, item

        return None


class ExtractorJobCorrelator(karton.core.Consumer):
    """ extractor job correlator """
    version = __version__

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
        self.jobs = []

        # cache key
        self.cache_criteria_key = None

        self.reports_key = config.config.get(
            "extractor", "reports_key", fallback="extractor.reports")
        self.job_cache_key = config.config.get(
            "extractor", "job_cache_key", fallback="extractor.cache:")

        identity = config.config.get(
            "extractor", "correlator_reports_identity",
            fallback="extractor.correlator-for-job-") + str(job_id)

        # avoid GracefulKiller from KartonServiceBase
        super(karton.core.base.KartonServiceBase, self).__init__(
            config=config, identity=identity, backend=backend)
        self.current_task = None
        self.setup_logger()
        self._pre_hooks = []
        self._post_hooks = []

    def process(self, task: karton.core.Task) -> None:
        peekaboo_job_id = task.get_payload("peekaboo-job-id")
        if peekaboo_job_id is not None and not isinstance(peekaboo_job_id, int):
            self.log.warning(
                "%s:%s: Dropping job with missing or invalid Peekaboo "
                "job ID", task.root_uid, peekaboo_job_id)
            return

        self.log.info("%s:%s: Correlating.", self.job_id, peekaboo_job_id)

        # do more schema checking here

        job = {}
        self.jobs.append(job)

        if peekaboo_job_id is not None:
            job["id"] = str(peekaboo_job_id)

        for datum in [
                "result", "reason", "report", "file-name", "content-type",
                "content-disposition", "extraction-level", "extracted-from",
                "root-sample", "sha256"]:
            value = task.get_payload(datum)
            if value is not None:
                job[datum] = value

        # not None, of type bool and True
        if job.get("root-sample") is True and job.get("sha256"):
            criteria_hash = hashlib.sha256(job.get("sha256").encode("ascii"))
            criteria_hash.update(
                job.get("content-disposition", "").encode("utf-8"))
            criteria_hash.update(job.get("content-type", "").encode("utf-8"))
            criteria_hash.update(job.get("file-name", "").encode("utf-8"))
            self.cache_criteria_key = criteria_hash.hexdigest()

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

        jobs_by_results = {}
        for job in self.jobs:
            result = job.get("result")
            if result is None:
                # consider jobs without result as failed
                result = "failed"

            if jobs_by_results.get(result) is None:
                jobs_by_results[result] = []

            jobs_by_results[result].append(job)

        # pick worst
        overall_result = None
        worst_job = None
        for result in ["bad", "failed", "good", "ignored", "unchecked", "unknown"]:
            jobs = jobs_by_results.get(result)

            # can be None or potentially empty list
            if jobs:
                overall_result = result
                worst_job = jobs[0]
                break

        job_info = dict(
            result="unknown",
            reason="unknown status from Peekaboo",
            report=[])

        # unknown result!?
        if worst_job is not None:
            job_info = {}
            job_info["result"] = overall_result
            reason = worst_job.get("reason")
            if reason is not None:
                job_info["reason"] = reason

            report = job_info["report"] = {}
            report["worst-job"] = worst_job
            report["jobs"] = self.jobs
        
        self.backend.redis.hset(
            self.reports_key, self.job_id, json.dumps(job_info))

        # add to cache
        if overall_result != "failed" and self.cache_criteria_key is not None:
            # extractor.cache:<sha256>[<job_id>] = <hours since epoch (float)>
            # cache collisions are no big deal because we need only *something*
            # to be cached not everyhing
            cache_key = self.job_cache_key + self.cache_criteria_key
            now = datetime.datetime.now(
                datetime.timezone.utc).timestamp() / 3600
            self.backend.redis.zadd(cache_key, {self.job_id: now})
            self.log.info(
                "%s: Recorded in cache as %s", self.job_id,
                self.cache_criteria_key)

        self.log.info("%s: Correlated.", self.job_id)
        self.backend.unregister_bind(self.identity)


class ExtractorCorrelator(karton.core.Consumer):
    """ extractor correlator """
    identity = "extractor.correlator"
    version = __version__
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
    ExtractorCorrelator.main()
