""" A Karton that holds back duplicate samples until a first has completed. """

import datetime
import hashlib
import sys

import karton
import karton.core

from . import __version__
from .common import DelayingKarton


class ExpanderDeduper(DelayingKarton):
    """ expander deduper karton """
    identity = "expander.deduper"
    version = __version__
    filters = [
        {
            "type": "expander-sample",
            "state": "new",
        }
    ]

    delay_filters_template = [
        {
            "type": "expander-sample",
            "state": "dedupe-delayed",
        },
        {
            "type": "expander-dedupe-hold",
        },
    ]

    def __init__(self, config=None, identity=None, backend=None, timeout=2):
        self.deduped_headers = {
            "type": "sample",
            "kind": "raw",
        }

        if config.getboolean("expander", "use_cache", fallback=True):
            self.deduped_headers = {
                "type": "expander-sample",
                "state": "deduped",
            }

        # give up deduping after ten minutes
        self.cutoff = datetime.timedelta(seconds=config.getint(
            "expanderdeduper", "cutoff", fallback=600))
        self.gc_interval = datetime.timedelta(seconds=config.getint(
            "expanderdeduper", "gc_interval",
            fallback=2*self.cutoff.total_seconds()))

        self.running_key = config.get(
            "expanderdeduper", "running_key", fallback="expander.running")
        self.reports_key = config.get(
            "expander", "reports_key", fallback="expander.reports")

        # re-check delayed dupes every other second.
        # Coupled to delay queue switch timeout since we are the only ones
        # injecting delayed tasks to ourselves and do not need to notice other
        # sources quickly.  Multiple instances of the deduper will
        # statistically increase the checking frequency and increase load on
        # redis. But they'd do so in any case.
        timeout = config.getint(
            "expanderdeduper", "recheck_interval", fallback=timeout)

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
        if self.last_gc + self.gc_interval > now:
            return

        # clear holds for which a report has materialized but the dedupe-hold
        # message has maybe been lost - can race each other without negative
        # impact
        running = self.backend.redis.hgetall(self.running_key)
        for criteria, job_id in running.copy().items():
            if not self.backend.redis.hexists(self.reports_key, job_id):
                continue

            self.log.info(
                "Clearing lock %s of finished job %s", criteria, job_id)
            self.backend.redis.hdel(self.running_key, criteria)
            del running[criteria]

        # clear out holds which still have no job report assigned in this
        # iteration at which point we're at least once GC interval and
        # therefore two times past the dedupe cutoff after which duplicates
        # started being passed through regardless of the hold anyway
        for criteria in self.orphaned_locks:
            job_id = self.backend.redis.hget(self.running_key, criteria)
            if job_id is None:
                continue

            self.log.info("Clearing orphaned lock %s", criteria)
            self.backend.redis.hdel(self.running_key, criteria)

        # running contains all jobs which had no report in this iteration
        self.orphaned_locks = running
        self.last_gc = now

    def process(self, task: karton.core.Task) -> None:
        self.collect_garbage()

        hold_headers = {
            "type": "expander-dedupe-hold",
            "delay-queue": self.delay_queues[self.next_delay_queue],
        }

        now = datetime.datetime.now(datetime.timezone.utc)

        task_type = task.headers.get("type")
        if task_type == "expander-dedupe-hold":
            criteria = task.get_payload("criteria")
            if criteria is None:
                self.log.warning(
                    "%s: Dropping dedupe hold without criteria", task.root_uid)
                return

            # clear the hold if a report has materialized
            if self.backend.redis.hexists(self.reports_key, task.root_uid):
                self.log.info(
                    "%s: Job has finished, clearing duplicate hold",
                    task.root_uid)
                self.backend.redis.hdel(self.running_key, criteria)
                return

            set_at_payload = task.get_payload("set-at")
            if set_at_payload is None:
                self.log.warning(
                    "%s: Dropping dedupe hold without set-at", task.root_uid)
                return

            set_at = datetime.datetime.fromisoformat(set_at_payload)

            # keep jobs from starving
            if set_at + self.cutoff <= now:
                self.log.info(
                    "%s: Job has potentially been blocking duplicates for %s "
                    "now - clearing hold", task.root_uid, now - set_at)
                self.backend.redis.hdel(self.running_key, criteria)
                return

            # poke ourselves again on next iteration
            task = task.derive_task(hold_headers)
            self.send_task(task)
            self.log.info(
                "%s: No new status on dedupe hold - looping though (%s)",
                task.root_uid, task.uid)
            return

        if task_type != "expander-sample":
            task = task.derive_task(self.deduped_headers)
            self.send_task(task)
            self.log.warning(
                "%s: Passing on task %s with unknown type: %s",
                task.root_uid, task.uid, task_type)
            return

        state = task.headers.get("state")
        if state is None:
            task = task.derive_task(self.deduped_headers)
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
            # try to lock new jobs - this together with hold removal on
            # detection of job completion above creates a kind of burst mode:
            # the first of duplicate samples will cause others to be held as
            # long its job is running. The hold is cleared when the job is
            # finished and all *held* duplicates will sail on through. But the
            # next *new* sample with the same criteria will set the hold again.
            # This should nicely limit the churn from duplicates whose jobs
            # keep failing and only marginally slow down responses for
            # successful jobs because the cache responder will very quickly
            # finish the job of the blocking duplicate so others can sail on
            # through again.
            if self.backend.redis.hsetnx(
                    self.running_key, criteria_key, task.root_uid):
                # pass on
                task = task.derive_task(self.deduped_headers)
                self.send_task(task)
                self.log.info(
                    "%s: Task is no dupe - passing on (%s)",
                    task.root_uid, task.uid)

                # poke ourselves to check if it finished so we can unblock held
                # duplicates
                task = karton.core.Task(
                    headers=hold_headers,
                    payload={
                        "criteria": criteria_key,
                        "set-at": now.isoformat(),
                    },
                )
                self.send_task(task)
                return

        # may have been deleted by dedupe hold being removed (even as a race
        # condition between setnx for new samples above and here, which is fine)
        job_id = self.backend.redis.hget(self.running_key, criteria_key)
        if job_id is None:
            task = task.derive_task(self.deduped_headers)
            self.send_task(task)
            self.log.info(
                "%s: Task is dupe but colliding job ID seems to have finished "
                "- passing on (%s)", task.root_uid, task.uid)
            return

        # report should provide cache hit (but will not for failed jobs - could
        # be checked and further serialised here)
        if self.backend.redis.hexists(self.reports_key, job_id):
            task = task.derive_task(self.deduped_headers)
            self.send_task(task)
            self.log.info(
                "%s: Task is dupe but colliding job %s is finished - passing "
                "on (%s)", task.root_uid, job_id, task.uid)
            return

        since_string = task.get_payload("dedupe-held-since")
        if since_string is None:
            # remember when this was seen first
            since_string = now.isoformat()
            task.add_payload("dedupe-held-since", since_string)

        # keep jobs from starving
        since = datetime.datetime.fromisoformat(since_string)
        if since + self.cutoff <= now:
            task = task.derive_task(self.deduped_headers)
            self.send_task(task)
            self.log.info(
                "%s: Task is dupe but colliding job %s has been blocking it "
                "for %s now - passing on (%s)", task.root_uid, job_id,
                now - since, task.uid)
            return

        task = task.derive_task({
            "type": "expander-sample",
            "state": "dedupe-delayed",
            "delay-queue": self.delay_queues[self.next_delay_queue],
        })
        self.send_task(task)
        self.log.info(
            "%s: Task is dupe - delaying (%s)", task.root_uid, task.uid)


if __name__ == "__main__":
    ExpanderDeduper.main()
