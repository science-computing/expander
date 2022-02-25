#!/home/michael/karton-venv/bin/python3

import datetime
import sys

import karton
import karton.core
import karton.core.task

import common

EXTRACTOR_PEEKABOO_JOBS = "extractor.peekaboo.jobs"

# 60 seconds should be way enough for all race-conditions to resolve themselves
RECHECK_CUTOFF = datetime.timedelta(seconds=60)

config = karton.core.Config(sys.argv[1])


class ExtractorPoker(common.DelayingKarton):
    identity = "extractor.poker"
    filters = [
        {
            "type": "peekaboo-job",
            "state": "new",
        }, 
        {
            "type": "peekaboo-job",
            "state": "done",
        }, 
    ]

    delay_filters_template = [
        {
            "type": "peekaboo-job",
            "state": "delayed",
        }, {
            "type": "peekaboo-job",
            "state": "done-recheck",
        }
    ]

    def __init__(self, config=None, identity=None, backend=None,
                 timeout=1, poking_delay=3):
        self.poking_delay = poking_delay

        super().__init__(
            config=config, identity=identity, backend=backend, timeout=timeout)

    def set_backend_delay_identity(self):
        delay_queue  = self.delay_queues[self.current_delay_queue]
        delay_filters =  self.delay_filters[delay_queue]

        # extractor.poker.delay-[one,two]
        delay_identity = self.delay_identity_template % delay_queue
        self.backend.set_delay_identity(delay_filters, delay_identity)

    def update_delay_queue(self):
        self.current_delay_queue = (self.current_delay_queue + 1
            ) % len(self.delay_queues)
        self.next_delay_queue = (self.current_delay_queue + 1
            ) % len(self.delay_queues)
        self.set_backend_delay_identity()

    def process(self, task: karton.core.Task) -> None:
        state = task.headers.get("state")
        if state is None:
            self.log.warning(
                "Ignoring task %s without state: %s", task.uid, task.headers)
            return

        peekaboo_job_id = task.get_payload("peekaboo-job-id")
        if peekaboo_job_id is not None and not isinstance(peekaboo_job_id, int):
            self.log.warning(
                "%s:%s: Dropping job with missing or invalid Peekaboo "
                "job ID", task.root_uid, peekaboo_job_id)
            return

        headers = {
            "type": "peekaboo-job",
            "state": "to-track",
            # either telling the tracker where to submit bounce-back jobs or
            # directly submitting there for ourselves to look at again next
            # iteration
            "delay-queue": self.delay_queues[self.next_delay_queue],
        }

        if state == "new":
            # correlate this Peekaboo job with its extractor job for deciding
            # when all spawned jobs are finished
            self.backend.redis.hincrby(
                EXTRACTOR_PEEKABOO_JOBS, task.root_uid, 1)

            # pass on to tracker, leave headers above as-is
            task = task.derive_task(headers)

            # remember in the payload when we saw it first
            task.add_payload("last-poke", datetime.datetime.now(
                datetime.timezone.utc).isoformat())

            self.send_task(task)
            self.log.info(
                "%s:%s: Initially poked Peekaboo job tracker (%s)",
                task.root_uid, peekaboo_job_id, task.uid)
            return

        if state == "delayed":
            if peekaboo_job_id is None:
                self.log.warning(
                    "Ignoring delay task %s without Peekaboo job ID: %s",
                    task.uid, task.headers)
                return

            last_string = task.get_payload("last-poke")
            if last_string is None:
                self.log.warning(
                    "%s:%s: Ignoring delayed Peekaboo job we've apparently "
                    "never seen before", task.root_uid, peekaboo_job_id)
                return

            last = datetime.datetime.fromisoformat(last_string)
            now = datetime.datetime.now(datetime.timezone.utc)
            poke_time = last + datetime.timedelta(seconds=self.poking_delay)

            if poke_time > now:
                # hand back to ourselves via the next delay queue for looking
                # at again
                headers["state"] = "delayed"
                self.log.info(
                    "%s:%s: Poking delay not yet expired, %s to go",
                    task.root_uid, peekaboo_job_id, poke_time - now)

                # do not update last-poke payload, so it expires
            else:
                # send to tracker for tracking and remember when we did so for
                # potential next delay
                task.remove_payload("last-poke")
                task.add_payload("last-poke", now.isoformat())

                self.log.info(
                    "%s:%s: Poking Peekaboo job tracker",
                    task.root_uid, peekaboo_job_id)

            task = task.derive_task(headers)
            self.send_task(task)
            return

        if state in ["done", "done-recheck"]:
            # clean up
            if state != "done-recheck":
                # do not spam the log on rechecks
                self.log.info(
                    "%s:%s: Removed Peekaboo job from tracking",
                    task.root_uid, peekaboo_job_id)

            # NOTE: we consider this an atomic operation which is preventing us
            # from producing multiple correlator pokes
            jobs_left = self.backend.redis.hincrby(
                EXTRACTOR_PEEKABOO_JOBS, task.root_uid, -1)
            if jobs_left < 0:
                self.log.warning(
                    "%s:%s: Job count corrupted (%d) - dropping job",
                    task.root_uid, peekaboo_job_id, jobs_left)
                self.backend.redis.hdel(EXTRACTOR_PEEKABOO_JOBS, task.root_uid)
                return

            if jobs_left != 0:
                return

            # make sure no more jobs are coming. This is quite an expensive
            # operation because we need to look at all tasks.
            all_tasks = self.backend.get_all_tasks()
            for other_task in all_tasks:
                if other_task.uid == task.uid:
                    # ignore ourselves
                    continue

                if other_task.root_uid != task.root_uid:
                    # ignore tasks with different root id
                    continue

                if other_task.status in [
                        karton.core.task.TaskState.FINISHED,
                        karton.core.task.TaskState.CRASHED]:
                    # ignore finished and crashed tasks
                    # NOTE: We ignore crashed tasks because they may not
                    # belong to our part of the pipeline and our getting to
                    # this point makes it extremely unlikely that it does.
                    continue

                if other_task.headers.get("type") in [
                        "peekaboo-report", "extractor-correlator-poke"]:
                    # ignore reports and correlator pokes
                    continue

                # NOTE: This check is vulnerable to dangling forks in the
                # pipeline, i.e. binds which are no longer serviced. Tasks
                # will be forked and routed to it and linger there in
                # Spawned state. The only way to detect that would be to
                # model the direct route of the pipeline up to the poker in
                # this check which defeats the purpose of having a flexible
                # pipeline. Therefore operators have to make sure to have
                # no dangling binds.
                now = datetime.datetime.now(datetime.timezone.utc)
                recheck_start = now
                recheck_start_payload = task.get_payload("recheck-start")
                if recheck_start_payload is not None:
                    recheck_start = datetime.datetime.fromisoformat(
                        recheck_start_payload)

                delay_and_recheck = True
                if recheck_start + RECHECK_CUTOFF < now:
                    self.log.warning(
                        "%s: Lingering task %s is blocking correlation.",
                        task.root_uid, other_task.uid)

                    if (other_task.status ==
                            karton.core.task.TaskState.SPAWNED):
                        self.log.info(
                            "%s: Blocker task is likely caused by "
                            "dangling bind. Ignoring and going ahead with "
                            "correlation after %s.",
                            task.root_uid, now - recheck_start)
                        delay_and_recheck = False
                else:
                    self.log.info(
                        "%s: More jobs appear to be coming: %s",
                        task.root_uid, other_task.uid)

                # there are race conditions between a submitter sending the
                # peekaboo-job task to us (and us having processed it) and
                # finishing its own task as well as a tracker sending off
                # the done task (and us having processed it) and finishing
                # its own task. This might leave the reports starving if we
                # just dropped this done task expecting another to be
                # produced by coming jobs. So we poke ourselves for the
                # next iteration.
                if delay_and_recheck:
                    # re-increment the pending jobs counter so upon re-check we
                    # can again atomically decide if the job count reached zero
                    # or not
                    self.backend.redis.hincrby(
                        EXTRACTOR_PEEKABOO_JOBS, task.root_uid, 1)

                    headers["state"] = "done-recheck"
                    task = task.derive_task(headers)

                    # add the first time we started re-checks only once
                    if not task.has_payload("recheck-start"):
                        task.add_payload(
                            "recheck-start", recheck_start.isoformat())

                    self.send_task(task)
                    return

            # all jobs tracked and reported - poke the correlator
            self.backend.redis.hdel(EXTRACTOR_PEEKABOO_JOBS, task.root_uid)
            task = karton.core.Task(
                headers={"type": "extractor-correlator-poke"})
            self.send_task(task)

            self.log.info(
                "%s: Poked correlator (%s)",
                task.root_uid, task.uid)
            return

        self.log.warning(
            "%s:%s: Ignoring task %s with unknown state %s",
            task.root_uid, peekaboo_job_id, task.uid, state)


if __name__ == "__main__":
    non_blocking_backend = common.DelayingKartonBackend(config)
    c = ExtractorPoker(config, backend=non_blocking_backend)
    c.loop()
