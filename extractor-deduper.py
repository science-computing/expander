#!/home/michael/karton-venv/bin/python3

import datetime
import hashlib
import sys

import karton
import karton.core

import common

EXTRACTOR_RUNNING = "extractor.running"
EXTRACTOR_RUNNING_SINCE = "extractor.running-since"
EXTRACTOR_REPORTS = "extractor.reports"

USE_CACHE = True

# re-check delayed dupes every other second.
# Coupled to delay queue switch timeout since we are the only ones injecting
# delayed tasks to ourselves and do not need to notice other sources quickly.
# Multiple instances of the deduper will statistically increase the checking
# frequency and increase load on redis. But they'd do so in any case.
DEDUPE_RECHECK = 2

# give up deduping after ten minutes
DEDUPE_CUTOFF = datetime.timedelta(seconds=60)

DEDUPE_GC_INTERVAL = 2 * DEDUPE_CUTOFF

config = karton.core.Config(sys.argv[1])


class ExtractorDeduper(common.DelayingKarton):
    identity = "extractor.deduper"
    filters = [
        {
            "type": "extractor-sample",
            "state": "new",
        }
    ]

    delay_filters_template = [
        {
            "type": "extractor-sample",
            "state": "dedupe-delayed",
        }
    ]

    def __init__(self, config=None, identity=None, backend=None, timeout=1):
        super().__init__(
            config=config, identity=identity, backend=backend, timeout=timeout)

        # do gc on first task
        self.last_gc = datetime.datetime(
            1970, 1, 1, tzinfo=datetime.timezone.utc)
        self.orphaned_locks = []

        # come to think of it, do gc now
        self.collect_garbage()

    def collect_garbage(self):
        now = datetime.datetime.now(datetime.timezone.utc)
        if self.last_gc + DEDUPE_GC_INTERVAL > now:
            return

        running = self.backend.redis.hgetall(EXTRACTOR_RUNNING)
        running_reverse = {}
        for criteria, job_id in running.items():
            running_reverse[job_id] = criteria

        running_since = self.backend.redis.hgetall(EXTRACTOR_RUNNING_SINCE)
        for job_id, timestamp in running_since.copy().items():
            # twice the cutoff makes them doubly invalid - they've been ignored
            # since the cutoff was reached
            since = datetime.datetime.fromisoformat(timestamp)
            if since + 2 * DEDUPE_CUTOFF < now:
                # backreference into running locks to keep them consistent
                criteria = running_reverse.get(job_id)
                if criteria is not None:
                    self.backend.redis.hdel(EXTRACTOR_RUNNING, criteria)
                    del running_reverse[job_id]
                    del running[criteria]

                self.log.info("Clearing old timestamp of job %s", job_id)
                self.backend.redis.hdel(EXTRACTOR_RUNNING_SINCE, job_id)
                del running_since[job_id]

        for criteria, job_id in running.copy().items():
            if not self.backend.redis.hexists(EXTRACTOR_REPORTS, job_id):
                continue

            self.log.info(
                "Clearing lock %s of finished job %s", criteria, job_id)
            self.backend.redis.hdel(EXTRACTOR_RUNNING, criteria)
            del running[criteria]

            # forward reference into timestamps to keep consistency
            if running_since.get(job_id) is not None:
                self.backend.redis.hdel(EXTRACTOR_RUNNING_SINCE, job_id)
                del running_since[job_id]

        # clear out locks which still have no timestamp assigned in this
        # iteration
        for criteria in self.orphaned_locks:
            job_id = self.backend.redis.hget(EXTRACTOR_RUNNING, criteria)
            if job_id is None:
                continue

            if self.backend.redis.hexists(EXTRACTOR_RUNNING_SINCE, job_id):
                continue

            self.log.info("Clearing orphaned lock %s", criteria)
            self.backend.redis.hdel(EXTRACTOR_RUNNING, criteria)

        self.orphaned_locks = []
        for criteria, job_id in running.items():
            if running_since.get(job_id) is not None:
                continue

            self.orphaned_locks.append(criteria)

        self.last_gc = now

    def process(self, task: karton.core.Task) -> None:
        self.collect_garbage()

        deduped_headers = {
            "type": "sample",
            "kind": "raw",
        }

        if USE_CACHE:
            deduped_headers = {
                "type": "extractor-sample",
                "state": "deduped",
            }

        state = task.headers.get("state")
        if state is None:
            task = task.derive_task(deduped_headers)
            self.send_task(task)
            self.log.warning(
                "%s: Passing on task %s without state: %s",
                task.root_uid, task.uid, task.headers)
            return

        sample = task.get_resource("sample")

        # file name can be None in resource
        file_name = sample.name
        if file_name is None:
            file_name = ""

        # condense all criteria what makes a duplicate into a single key.
        # Hashing a hexdigest again here seems counter-intuitive but it comes
        # pre-computed and is the main distinctive criterion for samples (but
        # not the only one). Unfortunately we can't just start off a new hashing
        # round at that state and advance it.
        sha256 = sample.sha256
        if sha256 is None:
            # basically impossible but not much code
            sha256 = hashlib.sha256(sample.content).hexdigest()

        criteria_hash = hashlib.sha256(sha256.encode("ascii"))
        criteria_hash.update(
            task.get_payload("content-disposition", "").encode("utf-8"))
        criteria_hash.update(
            task.get_payload("content-type", "").encode("utf-8"))
        criteria_hash.update(file_name.encode("utf-8"))
        criteria_key = criteria_hash.hexdigest()

        # new and delayed tasks are treated (almost) the same for code
        # efficiency and robustness
        if state == "new":
            # try to lock new jobs
            if self.backend.redis.hsetnx(
                    EXTRACTOR_RUNNING, criteria_key, task.root_uid):
                # remember when this was seen
                now = datetime.datetime.now(datetime.timezone.utc)
                since = now.isoformat()
                self.backend.redis.hset(
                    EXTRACTOR_RUNNING_SINCE, task.root_uid, since)

                # pass on
                task = task.derive_task(deduped_headers)
                self.send_task(task)
                self.log.info(
                    "%s: Task is no dupe - passing on (%s)",
                    task.root_uid, task.uid)
                return

        job_id = self.backend.redis.hget(EXTRACTOR_RUNNING, criteria_key)
        if job_id is None:
            task = task.derive_task(deduped_headers)
            self.send_task(task)
            self.log.info(
                "%s: Task is dupe but colliding job ID is "
                "(inconsistently) not recorded - passing on (%s)",
                task.root_uid, task.uid)
            return

        # report should provide cache hit (but will not for failed jobs - could
        # be checked and further serialised here)
        if self.backend.redis.hexists(EXTRACTOR_REPORTS, job_id):
            task = task.derive_task(deduped_headers)
            self.send_task(task)
            self.log.info(
                "%s: Task is dupe but colliding job %s is finished - passing "
                "on (%s)", task.root_uid, job_id, task.uid)
            return

        since_string = self.backend.redis.hget(
            EXTRACTOR_RUNNING_SINCE, job_id)
        if since_string is None:
            task = task.derive_task(deduped_headers)
            self.send_task(task)
            self.log.info(
                "%s: Task is dupe colliding job %s start time is "
                "(inconsistently) not recorded - passing on (%s)",
                task.root_uid, job_id, task.uid)
            return

        # keep jobs from starving
        now = datetime.datetime.now(datetime.timezone.utc)
        since = datetime.datetime.fromisoformat(since_string)
        if since + DEDUPE_CUTOFF <= now:
            task = task.derive_task(deduped_headers)
            self.send_task(task)
            self.log.info(
                "%s: Task is dupe but colliding job %s has been blocking it "
                "for %s now - passing on (%s)", task.root_uid, job_id,
                now - since, task.uid)
            return

        task = task.derive_task({
            "type": "extractor-sample",
            "state": "dedupe-delayed",
            "delay-queue": self.delay_queues[self.next_delay_queue],
        })
        self.send_task(task)
        self.log.info(
            "%s: Task is dupe - delaying (%s)", task.root_uid, task.uid)


if __name__ == "__main__":
    non_blocking_backend = common.DelayingKartonBackend(config)
    c = ExtractorDeduper(
        config, backend=non_blocking_backend, timeout=DEDUPE_RECHECK)
    c.loop()
