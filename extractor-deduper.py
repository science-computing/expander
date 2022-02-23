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

    def process(self, task: karton.core.Task) -> None:
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

                # FIXME: We're accumulating two hash keys here which need
                # garbage-collecting.

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
    non_blocking_backend = common.NonBlockingDelayingKartonBackend(config)
    c = ExtractorDeduper(
        config, backend=non_blocking_backend, timeout=DEDUPE_RECHECK)
    c.loop()
